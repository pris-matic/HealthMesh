from datetime import timedelta
from random import Random
from uuid import UUID

import pytest

from healthmesh.domain.snapshot import build_snapshot
from healthmesh.domain.types import PlanningFact
from healthmesh.planner.service import plan, validate_proposal
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident


@pytest.fixture
def snapshot(db, django_user_model):
    incident = create_incident(load_scenario('storm-supply-01'), django_user_model.objects.create_user('planner'))
    return build_snapshot(incident.id)


def fact(snapshot, value, status='HUMAN_VERIFIED'):
    # Counterfactual facts explicitly carry test provenance; canonical claim/value
    # linkage is validated by the snapshot-building boundary, not this pure unit test.
    return PlanningFact(value=value, status=status, observed_at=snapshot.as_of,
                        evidence_id=UUID(int=9001), source_id=snapshot.source_ids[0])


def verified(snapshot, brook=90, cedar=55):
    batches = tuple(b.model_copy(update={'on_hand': fact(snapshot, brook if b.facility_name == 'Brook' else cedar)})
                    if b.facility_name != 'Aster' else b for b in snapshot.batches)
    routes = tuple(r.model_copy(update={'status': fact(snapshot, 'OPEN')}) for r in snapshot.routes)
    return snapshot.model_copy(update={'batches': batches, 'routes': routes})


def test_fixture_yields_two_explicit_provisional_branches(snapshot):
    before = snapshot.model_dump_json()
    result = plan(snapshot)
    assert result.status == 'PROVISIONAL'
    assert len(result.branches) == 2
    by_name = {b.allocations[0].facility_name: b for b in result.branches}
    assert all(b.status == 'PROVISIONAL' and b.conditions for b in by_name.values())
    assert any(c.field == 'on_hand' and c.status == 'UNKNOWN' for c in by_name['Brook'].checks)
    assert any(c.field == 'route_status' and c.status == 'UNKNOWN' for c in by_name['Cedar'].checks)
    assert result.mandatory_unverified_facts and result.evidence_ids and result.requires_review
    assert result.snapshot_id == snapshot.snapshot_id and result.revision == snapshot.revision
    assert plan(snapshot) == result
    assert snapshot.model_dump_json() == before


def test_verified_brook_90_can_supply_40(snapshot):
    result = plan(verified(snapshot))
    brook = next(b for b in result.branches if b.allocations[0].facility_name == 'Brook')
    assert result.status == brook.status == 'READY_FOR_REVIEW'
    assert brook.allocations[0].transferable == 45
    assert brook.allocations[0].quantity == 40


def test_two_sources_split_when_each_below_40(snapshot):
    snapshot = verified(snapshot, brook=65, cedar=35)
    ready = [b for b in plan(snapshot).branches if b.status == 'READY_FOR_REVIEW']
    assert ready
    assert all(len(b.allocations) == 2 and sum(a.quantity for a in b.allocations) == 40 for b in ready)
    assert all(a.quantity <= a.transferable for b in ready for a in b.allocations)


@pytest.mark.parametrize('provenance', ['evidence_id', 'source_id', 'foreign_source'])
@pytest.mark.parametrize('subject,field', [
    ('request', 'quantity'), ('request', 'need_by'), ('request', 'is_net_transfer_request'),
    ('batch', 'on_hand'), ('batch', 'reserved'), ('batch', 'protected_reserve'),
    ('batch', 'reserve_accounting'), ('batch', 'expires_on'), ('batch', 'storage_usable'),
    ('route', 'status'), ('route', 'verified_open_eta_hours'),
])
def test_mandatory_facts_without_provenance_never_ready(snapshot, provenance, subject, field):
    current = verified(snapshot)
    branch = next(b for b in plan(current).branches if b.status == 'READY_FOR_REVIEW')
    change = {'source_id': UUID(int=9002)} if provenance == 'foreign_source' else {provenance: None}

    def unsupported(record):
        return record.model_copy(update={field: getattr(record, field).model_copy(update=change)})

    if subject == 'request':
        current = current.model_copy(update={'request': unsupported(current.request)})
    elif subject == 'batch':
        current = current.model_copy(update={'batches': tuple(unsupported(b) for b in current.batches)})
    else:
        current = current.model_copy(update={'routes': tuple(unsupported(r) for r in current.routes)})
    checked_field = 'route_status' if subject == 'route' and field == 'status' else field
    assert any(c.field == checked_field and c.status == 'UNKNOWN' for c in validate_proposal(current, branch))
    assert plan(current).status != 'READY_FOR_REVIEW'


def test_provisional_single_does_not_hide_verified_full_fill_pair(snapshot):
    current = verified(snapshot, brook=65, cedar=30)
    template = next(b for b in current.batches if b.facility_name == 'Brook')
    extra = template.model_copy(update={'id': UUID(int=9100), 'facility_id': UUID(int=9101),
                                       'facility_name': 'Dawn', 'on_hand': fact(current, None, 'UNKNOWN')})
    route = next(r for r in current.routes if r.origin_id == template.facility_id)
    extra_route = route.model_copy(update={'id': UUID(int=9102), 'origin_id': extra.facility_id, 'origin_name': 'Dawn'})
    current = current.model_copy(update={'batches': (*current.batches, extra), 'routes': (*current.routes, extra_route)})
    result = plan(current)
    assert any(b.status == 'PROVISIONAL' and len(b.allocations) == 1 for b in result.branches)
    assert result.status == 'READY_FOR_REVIEW'
    ready = result.branches[0]
    assert ready.status == 'READY_FOR_REVIEW'
    assert {a.facility_name: a.quantity for a in ready.allocations} == {'Brook': 20, 'Cedar': 20}


@pytest.mark.parametrize('field,value,status', [
    ('expires_on', 'expired', 'HUMAN_VERIFIED'), ('storage_usable', False, 'HUMAN_VERIFIED'),
    ('on_hand', 1000, 'UNKNOWN'), ('reserved', None, 'UNKNOWN'),
    ('protected_reserve', None, 'UNKNOWN'), ('expires_on', None, 'UNKNOWN'),
    ('storage_usable', None, 'UNKNOWN'), ('reserve_accounting', 'included', 'HUMAN_VERIFIED'),
    ('on_hand', 1000, 'STALE'),
])
def test_expired_closed_route_or_unknown_mandatory_fact_never_ready(snapshot, field, value, status):
    snapshot = verified(snapshot)
    if value == 'expired':
        value = snapshot.as_of.date() - timedelta(days=1)
    batches = tuple(b.model_copy(update={field: fact(snapshot, value, status)}) for b in snapshot.batches)
    result = plan(snapshot.model_copy(update={'batches': batches}))
    assert result.status != 'READY_FOR_REVIEW'
    assert all(b.status != 'READY_FOR_REVIEW' for b in result.branches)


@pytest.mark.parametrize('status', ['CLOSED', 'UNKNOWN'])
def test_route_failure_never_ready(snapshot, status):
    snapshot = verified(snapshot)
    routes = tuple(r.model_copy(update={'status': fact(snapshot, status)}) for r in snapshot.routes)
    assert plan(snapshot.model_copy(update={'routes': routes})).status != 'READY_FOR_REVIEW'


def test_zero_candidates_and_missing_request(snapshot):
    assert plan(snapshot.model_copy(update={'batches': ()})).status == 'NO_FEASIBLE_OPTION'
    assert plan(snapshot.model_copy(update={'request': None})).status == 'BLOCKED_INSUFFICIENT_EVIDENCE'


def test_guard_rejects_tampering_partial_duplicate_and_wrong_snapshot(snapshot):
    snapshot = verified(snapshot)
    branch = next(b for b in plan(snapshot).branches if b.status == 'READY_FOR_REVIEW')
    allocation = branch.allocations[0]
    for edited in [branch.model_copy(update={'allocations': (allocation.model_copy(update={'quantity': 1000}),)}),
                   branch.model_copy(update={'allocations': (allocation.model_copy(update={'quantity': 1}),)}),
                   branch.model_copy(update={'allocations': (allocation, allocation)}),
                   branch.model_copy(update={'revision': snapshot.revision + 1})]:
        assert any(c.status == 'FAIL' for c in validate_proposal(snapshot, edited))


def test_unit_product_deadline_and_freshness_fail_closed(snapshot):
    snapshot = verified(snapshot)
    for changes in [{'base_unit': 'bottle'}, {'product_id': snapshot.incident_id},
                    {'on_hand': fact(snapshot, 100).model_copy(update={'observed_at': snapshot.as_of - timedelta(days=9)})}]:
        batches = tuple(b.model_copy(update=changes) for b in snapshot.batches)
        assert plan(snapshot.model_copy(update={'batches': batches})).status != 'READY_FOR_REVIEW'
    request = snapshot.request.model_copy(update={'need_by': fact(snapshot, snapshot.as_of)})
    assert plan(snapshot.model_copy(update={'request': request})).status != 'READY_FOR_REVIEW'


def test_generated_ready_allocations_preserve_verified_stock_and_balances(snapshot):
    rng = Random(314159)
    for _ in range(200):
        current = verified(snapshot, rng.randrange(151), rng.randrange(151))
        batches = tuple(b.model_copy(update={'reserved': fact(current, rng.randrange(61)),
                                             'protected_reserve': fact(current, rng.randrange(61))}) for b in current.batches)
        current = current.model_copy(update={'batches': batches})
        for branch in plan(current).branches:
            if branch.status != 'READY_FOR_REVIEW':
                continue
            assert sum(a.quantity for a in branch.allocations) == 40
            assert all(c.status == 'PASS' for c in validate_proposal(current, branch))
            for allocation in branch.allocations:
                batch = next(b for b in batches if b.id == allocation.batch_id)
                assert 0 < allocation.quantity <= batch.on_hand.value
                assert batch.on_hand.value - batch.reserved.value - allocation.quantity >= batch.protected_reserve.value
