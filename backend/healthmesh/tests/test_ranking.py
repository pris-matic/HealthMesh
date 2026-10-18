"""Public-world question ranking and revision-neutral task selection."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from healthmesh.domain.snapshot import build_snapshot
from healthmesh.domain.types import PlanningFact
from healthmesh.planner.service import plan
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(django_user_model):
    coordinator = django_user_model.objects.create_user('question-coordinator')
    coordinator.groups.add(Group.objects.get_or_create(name='coordinator')[0])
    reviewer = django_user_model.objects.create_user('question-reviewer')
    reviewer.groups.add(Group.objects.get_or_create(name='reviewer')[0])
    incident = create_incident(load_scenario('storm-supply-01'), coordinator)
    snapshot = build_snapshot(incident.id)
    return SimpleNamespace(incident=incident, snapshot=snapshot, coordinator=coordinator,
                           reviewer=reviewer, client=Client())


def test_public_world_sensitivity_selects_brook_without_probabilities(case):
    from healthmesh.evidence.ranking import rank_verifications

    before = case.snapshot.model_dump_json()
    ranking = rank_verifications(case.snapshot, plan(case.snapshot), case.snapshot.policy.verification_budget)
    assert ranking.valuation_mode == 'DETERMINISTIC_SENSITIVITY'
    assert ranking.net_value_estimate is None
    assert ranking.must_verify_before_approval is True
    assert [(task.facility_name, task.field, task.sensitivity_count, task.staff_minutes)
            for task in ranking.tasks] == [('Brook', 'on_hand', 2, 3), ('Cedar', 'route_status', 1, 5)]
    brook = ranking.tasks[0]
    assert brook.subject_id == next(batch.id for batch in case.snapshot.batches if batch.facility_name == 'Brook')
    assert brook.evidence_ids
    assert {effect.answer for effect in brook.possible_effects} == {'70', '90'}
    assert {effect.answer: effect.target_branch_status for effect in brook.possible_effects} == {
        '70': 'INFEASIBLE', '90': 'READY_FOR_REVIEW'}
    assert any('READY_FOR_REVIEW' in effect.branch_statuses for effect in brook.possible_effects)
    assert any('INFEASIBLE' in effect.branch_statuses for effect in brook.possible_effects)
    assert case.snapshot.model_dump_json() == before


def test_cedar_open_does_not_change_preferred_action_when_brook_90_is_ready(case):
    from healthmesh.evidence.ranking import rank_verifications

    ranked = rank_verifications(case.snapshot, plan(case.snapshot), case.snapshot.policy.verification_budget)
    cedar = next(task for task in ranked.tasks if task.facility_name == 'Cedar')
    assert cedar.sensitivity_count == 1
    assert cedar.sensitivity_per_staff_minute == pytest.approx(1 / 5)


def test_invariant_answers_and_expired_queries_are_not_optional(case):
    from healthmesh.evidence.ranking import rank_verifications

    policy = case.snapshot.policy.model_copy(update={'public_possibilities':
        case.snapshot.policy.public_possibilities.model_copy(update={'brook_on_hand': (90, 95),
                                                                     'cedar_route': ('CLOSED',)})})
    invariant = case.snapshot.model_copy(update={'policy': policy})
    ranking = rank_verifications(invariant, plan(invariant), invariant.policy.verification_budget)
    assert ranking.tasks == ()
    assert ranking.must_verify_before_approval is True

    late = case.snapshot.model_copy(update={'request': case.snapshot.request.model_copy(update={
        'need_by': case.snapshot.request.need_by.model_copy(update={'value': case.snapshot.as_of + timedelta(minutes=9)})})})
    assert rank_verifications(late, plan(late), late.policy.verification_budget).tasks == ()


def test_existing_ready_brook_plan_does_not_query_irrelevant_cedar_route(case):
    from healthmesh.evidence.ranking import rank_verifications

    brook = next(batch for batch in case.snapshot.batches if batch.facility_name == 'Brook')
    confirmed = brook.model_copy(update={'on_hand': PlanningFact(value=90, status='HUMAN_VERIFIED',
        observed_at=case.snapshot.as_of, evidence_id=brook.on_hand.evidence_id,
        source_id=brook.on_hand.source_id)})
    current = case.snapshot.model_copy(update={'batches': tuple(
        confirmed if row.id == brook.id else row for row in case.snapshot.batches)})
    result = plan(current)
    assert result.status == 'READY_FOR_REVIEW'
    ranked = rank_verifications(current, result, current.policy.verification_budget)
    assert ranked.tasks == ()
    assert ranked.must_verify_before_approval is False


def test_verified_label_without_provenance_still_leaves_brook_question_available(case):
    from healthmesh.evidence.ranking import rank_verifications

    brook = next(batch for batch in case.snapshot.batches if batch.facility_name == 'Brook')
    unsupported = brook.model_copy(update={'on_hand': PlanningFact(value=90, status='HUMAN_VERIFIED',
        observed_at=case.snapshot.as_of, evidence_id=None, source_id=None)})
    current = case.snapshot.model_copy(update={'batches': tuple(
        unsupported if row.id == brook.id else row for row in case.snapshot.batches)})
    ranked = rank_verifications(current, plan(current), current.policy.verification_budget)
    assert ranked.tasks and ranked.tasks[0].facility_name == 'Brook'


def test_mandatory_signoff_gate_is_distinct_from_optional_query_value(case):
    from healthmesh.evidence.ranking import rank_verifications

    brook = next(batch for batch in case.snapshot.batches if batch.facility_name == 'Brook')
    cedar = next(route for route in case.snapshot.routes if route.origin_name == 'Cedar')
    def confirmed(value, prior):
        return PlanningFact(value=value, status='HUMAN_VERIFIED', observed_at=case.snapshot.as_of,
                            evidence_id=prior.evidence_id, source_id=prior.source_id)
    current = case.snapshot.model_copy(update={
        'batches': tuple(row.model_copy(update={'on_hand': confirmed(70, row.on_hand)})
                         if row.id == brook.id else row for row in case.snapshot.batches),
        'routes': tuple(row.model_copy(update={'status': confirmed('CLOSED', row.status)})
                        if row.id == cedar.id else row for row in case.snapshot.routes)})
    result = plan(current)
    assert result.status == 'NO_FEASIBLE_OPTION' and not result.mandatory_unverified_facts
    ranking = rank_verifications(current, result, current.policy.verification_budget)
    assert ranking.tasks == ()
    assert ranking.must_verify_before_approval is False


def test_public_possible_worlds_are_bounded(case):
    from healthmesh.scenarios.fixture import PublicPossibilities

    with pytest.raises(ValueError):
        PublicPossibilities(synthetic=True, brook_on_hand=(70, 80, 90), cedar_route=('CLOSED', 'OPEN'))


def test_missing_deadline_cannot_admit_a_question(case):
    from healthmesh.evidence.ranking import rank_verifications

    request = case.snapshot.request.model_copy(update={
        'need_by': case.snapshot.request.need_by.model_copy(update={'value': None, 'status': 'UNKNOWN'})})
    current = case.snapshot.model_copy(update={'request': request})
    ranked = rank_verifications(current, plan(current), current.policy.verification_budget)
    assert ranked.tasks == ()


def test_one_question_cannot_value_a_branch_that_needs_both_answers(case):
    from healthmesh.evidence.ranking import rank_verifications

    cedar = next(batch for batch in case.snapshot.batches if batch.facility_name == 'Cedar')
    reduced = cedar.model_copy(update={'on_hand': PlanningFact(value=30, status='HUMAN_VERIFIED',
        observed_at=case.snapshot.as_of, evidence_id=cedar.on_hand.evidence_id,
        source_id=cedar.on_hand.source_id)})
    possibilities = case.snapshot.policy.public_possibilities.model_copy(update={'brook_on_hand': (60, 70)})
    current = case.snapshot.model_copy(update={
        'batches': tuple(reduced if row.id == cedar.id else row for row in case.snapshot.batches),
        'policy': case.snapshot.policy.model_copy(update={'public_possibilities': possibilities})})
    ranked = rank_verifications(current, plan(current), current.policy.verification_budget)
    assert ranked.tasks == ()


def test_selected_question_is_stable_revision_neutral_and_exhausts_one_question_budget(case):
    from healthmesh.models import InventoryClaim, SourceDocument, VerificationTask

    case.client.force_login(case.coordinator)
    path = f'/api/v1/incidents/{case.incident.id}/verification-tasks'
    claim_count = InventoryClaim.objects.count()
    source_count = SourceDocument.objects.count()
    listed = case.client.get(path)
    assert listed.status_code == 200
    options = listed.json()['tasks']
    assert len(options) == 2
    selected = options[0]
    payload = {'subject_id': selected['subject_id'], 'field': selected['field'],
               'expected_revision': case.snapshot.revision}
    first = case.client.post(path, json.dumps(payload), content_type='application/json')
    assert first.status_code == 201
    task = first.json()['selected_task']
    assert task['subject_id'] == selected['subject_id']
    assert task['field'] == 'on_hand'
    assert task['selected_snapshot_revision'] == case.snapshot.revision
    assert task['staff_minutes'] == 3 and task['response_delay_minutes'] == 10
    assert task['due_at'] == (case.snapshot.as_of + timedelta(minutes=10)).isoformat()
    assert VerificationTask.objects.count() == 1
    assert InventoryClaim.objects.count() == claim_count
    assert SourceDocument.objects.count() == source_count
    assert case.client.post(path, json.dumps(payload), content_type='application/json').json()['selected_task']['id'] == task['id']
    case.incident.refresh_from_db()
    assert case.incident.revision == case.snapshot.revision
    assert VerificationTask.objects.count() == 1

    other = options[1]
    denied = case.client.post(path, json.dumps({'subject_id': other['subject_id'], 'field': other['field'],
        'expected_revision': case.snapshot.revision}), content_type='application/json')
    assert denied.status_code == 409 and denied.json()['code'] == 'NO_POSITIVE_OPTIONAL_QUERY'


def test_selection_rejects_stale_revision_and_wrong_role(case):
    case.client.force_login(case.reviewer)
    path = f'/api/v1/incidents/{case.incident.id}/verification-tasks'
    brook = next(batch for batch in case.snapshot.batches if batch.facility_name == 'Brook')
    payload = {'subject_id': str(brook.id), 'field': 'on_hand',
               'expected_revision': case.snapshot.revision}
    assert case.client.post(path, json.dumps(payload), content_type='application/json').status_code == 403
    case.client.force_login(case.coordinator)
    stale = {**payload, 'expected_revision': case.snapshot.revision - 1}
    response = case.client.post(path, json.dumps(stale), content_type='application/json')
    assert response.status_code == 409 and response.json()['code'] == 'stale_snapshot'


def test_weighted_worlds_require_valid_explicit_weights(case):
    from healthmesh.evidence.ranking import rank_verifications
    from healthmesh.scenarios.fixture import PublicWorldWeight

    worlds = tuple(PublicWorldWeight(brook_on_hand=brook, cedar_route=cedar, weight=0.25)
                   for brook in (70, 90) for cedar in ('CLOSED', 'OPEN'))
    possibilities = case.snapshot.policy.public_possibilities.model_copy(update={'weights': worlds})
    weighted = case.snapshot.model_copy(update={'policy': case.snapshot.policy.model_copy(update={
        'public_possibilities': possibilities})})
    ranking = rank_verifications(weighted, plan(weighted), weighted.policy.verification_budget)
    assert ranking.valuation_mode == 'WEIGHTED_EXPECTED_LOSS'
    assert ranking.tasks[0].facility_name == 'Brook'
    # Without a query: 40 unmet boxes * 100 = 4000. Brook verification
    # resolves half the weighted worlds; expected travel is 1 hour and the
    # 3 staff minutes / 10 minute delay are charged once across both worlds.
    assert ranking.net_value_estimate == pytest.approx(4000 - (2000 + 1 + .6 + 10 / 60))
    assert ranking.tasks[0].net_value_estimate == ranking.net_value_estimate
    invalid = possibilities.model_copy(update={'weights': worlds[:3]})
    bad = weighted.model_copy(update={'policy': weighted.policy.model_copy(update={'public_possibilities': invalid})})
    with pytest.raises(ValueError):
        rank_verifications(bad, plan(bad), bad.policy.verification_budget)
