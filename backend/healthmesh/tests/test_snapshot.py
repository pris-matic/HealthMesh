from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
import pytest

from healthmesh.models import CanonicalFact, Incident, InventoryClaim, SourceDocument, SourceSpan
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident
from healthmesh.domain.snapshot import build_snapshot

pytestmark = pytest.mark.django_db


@pytest.fixture
def incident(django_user_model):
    return create_incident(load_scenario('storm-supply-01'), django_user_model.objects.create_user('synthetic'))


def test_snapshot_preserves_old_and_new_claims(incident):
    before = build_snapshot(incident.id)
    brook = next(batch for batch in before.batches if batch.facility_name == 'Brook')
    note = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    candidate = InventoryClaim.objects.create(
        incident=incident, subject_id=brook.id, field='on_hand', value=80,
        source=note, observed_at=note.observed_at, status='OBSERVED_UNVERIFIED', basis='unsigned test candidate',
    )
    snapshot = build_snapshot(incident.id)
    counts = [claim for claim in snapshot.claims if claim.subject_id == brook.id and claim.field == 'on_hand']
    assert {claim.value for claim in counts} == {95, 80}
    assert next(claim for claim in counts if claim.id == candidate.id).status == 'OBSERVED_UNVERIFIED'
    current = next(batch for batch in snapshot.batches if batch.id == brook.id).on_hand
    assert current.value == 95
    assert current.status == 'STALE'
    assert current.evidence_id != candidate.id
    assert snapshot.revision == before.revision + 1


@pytest.mark.parametrize(('field', 'value'), [('on_hand', -1), ('reserved', -1), ('protected_reserve', -1), ('reserve_accounting', 'includes_protected_reserve')])
def test_negative_or_double_counted_reserve_rejected(incident, field, value):
    batch = build_snapshot(incident.id).batches[0]
    fact = CanonicalFact.objects.get(incident=incident, subject_id=batch.id, field=field)
    fact.value = value
    with pytest.raises(ValidationError):
        fact.save()
    claim = InventoryClaim.objects.get(id=fact.claim_id)
    with pytest.raises(ValidationError):
        InventoryClaim.objects.create(incident=incident, subject_id=batch.id, field=field, value=value,
                                      source=claim.source, observed_at=claim.observed_at, basis='invalid test')


def test_rebuild_same_revision_is_identical(incident):
    first = build_snapshot(incident.id)
    assert first.model_dump_json() == build_snapshot(incident.id).model_dump_json()
    assert first.snapshot_id == f'{incident.id}:{first.revision}'
    assert first.as_of.isoformat() == '2026-10-17T12:00:00+00:00'


def test_seed_preserves_signed_demand_and_raw_note_without_extraction(incident):
    snapshot = build_snapshot(incident.id)
    assert len(snapshot.batches) == 3
    assert snapshot.request.quantity.value == 40
    assert snapshot.request.is_net_transfer_request.value is True
    assert not any(claim.value == 80 for claim in snapshot.claims)
    for facility in snapshot.facilities:
        assert len(facility.daily_demand.value) == 14
        source = SourceDocument.objects.get(id=facility.daily_demand.source_id)
        assert source.kind == 'SIGNED_FIXTURE'
        assert source.fixture_id == next(f.demand_source_id for f in load_scenario('storm-supply-01').public_facts.facilities if f.name == facility.name)
    note = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    assert '80 boxes' in note.decoded_text
    assert bytes(note.original_blob).decode('utf-8') == note.decoded_text
    assert not InventoryClaim.objects.filter(source=note).exists()
    cedar = next(route for route in snapshot.routes if route.origin_name == 'Cedar')
    assert cedar.status.value == 'UNKNOWN'
    assert cedar.status.status == 'UNKNOWN'
    assert {task.facility_id for task in snapshot.policy.verification_tasks} <= {f.id for f in snapshot.facilities}


@pytest.mark.parametrize('model', [SourceDocument, SourceSpan, InventoryClaim])
def test_evidence_is_append_only(incident, model):
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    record = (source if model is SourceDocument else
              SourceSpan.objects.create(source=source, start=0, end=9) if model is SourceSpan else
              InventoryClaim.objects.filter(incident=incident).first())
    with pytest.raises(ValidationError):
        record.save()
    with pytest.raises(ValidationError):
        record.delete()
    with pytest.raises(ValidationError):
        model.objects.filter(pk=record.pk).update(id=record.pk)
    with pytest.raises(ValidationError):
        model.objects.filter(pk=record.pk).delete()


def test_unicode_spans_are_half_open_and_validated(incident):
    source = SourceDocument.objects.create(incident=incident, kind='RAW_TEXT', decoded_text='é😀80 boxes',
                                          original_blob='é😀80 boxes'.encode(), observed_at=incident.simulated_now)
    span = SourceSpan.objects.create(source=source, start=2, end=4)
    assert span.text == '80'
    for start, end in [(-1, 2), (4, 2), (0, 11)]:
        with pytest.raises(ValidationError):
            SourceSpan.objects.create(source=source, start=start, end=end)


def test_missing_fact_fails_closed_and_clock_advances_revision(incident):
    before = build_snapshot(incident.id)
    brook = next(batch for batch in before.batches if batch.facility_name == 'Brook')
    fact = CanonicalFact.objects.get(incident=incident, subject_id=brook.id, field='storage_usable')
    fact.delete()
    unknown = next(batch for batch in build_snapshot(incident.id).batches if batch.id == brook.id).storage_usable
    assert unknown.value is None and unknown.status == 'UNKNOWN'
    incident.refresh_from_db()
    old_revision = incident.revision
    incident.simulated_now += timedelta(days=2)
    incident.save()
    after = build_snapshot(incident.id)
    assert after.revision == old_revision + 1
    assert after.as_of == incident.simulated_now
    assert all(batch.on_hand.status == 'STALE' for batch in after.batches)
    incident.simulated_now -= timedelta(days=1)
    with pytest.raises(ValidationError):
        incident.save()


def test_repeated_scenario_has_isolated_identities(incident, django_user_model):
    other = create_incident(load_scenario('storm-supply-01'), django_user_model.objects.get(username='synthetic'))
    first, second = build_snapshot(incident.id), build_snapshot(other.id)
    assert set(first.source_ids).isdisjoint(second.source_ids)
    assert {batch.id for batch in first.batches}.isdisjoint(batch.id for batch in second.batches)
    fact = CanonicalFact.objects.filter(incident=incident).first()
    fact.source = SourceDocument.objects.filter(incident=other).first()
    with pytest.raises(ValidationError):
        fact.save()


def test_canonical_fact_cannot_promote_unsigned_or_change_claim_value(incident):
    note = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    batch = build_snapshot(incident.id).batches[0]
    claim = InventoryClaim.objects.create(incident=incident, subject_id=batch.id, field='on_hand', value=80,
                                          source=note, observed_at=note.observed_at, basis='unsigned')
    fact = CanonicalFact.objects.get(incident=incident, subject_id=batch.id, field='on_hand')
    fact.claim, fact.source, fact.value, fact.observed_at = claim, note, 80, note.observed_at
    fact.status = 'HUMAN_VERIFIED'
    with pytest.raises(ValidationError):
        fact.save()
    fact.status, fact.value = 'OBSERVED_UNVERIFIED', 90
    with pytest.raises(ValidationError):
        fact.save()


def test_bulk_mutations_cannot_bypass_revision_or_validation(incident):
    with pytest.raises(ValidationError):
        CanonicalFact.objects.filter(incident=incident).update(value=-1)
    with pytest.raises(ValidationError):
        Incident.objects.filter(pk=incident.id).update(simulated_now=incident.simulated_now + timedelta(days=1))


def test_cross_incident_claim_subject_rejected(incident, django_user_model):
    other = create_incident(load_scenario('storm-supply-01'), django_user_model.objects.get(username='synthetic'))
    batch = build_snapshot(other.id).batches[0]
    source = SourceDocument.objects.filter(incident=incident).first()
    with pytest.raises(ValidationError):
        InventoryClaim.objects.create(incident=incident, subject_id=batch.id, field='on_hand', value=10,
                                      source=source, observed_at=source.observed_at, basis='invalid subject')


def test_invalid_request_time_cannot_be_canonicalized(incident):
    fact = CanonicalFact.objects.get(incident=incident, field='need_by')
    with pytest.raises(ValidationError):
        InventoryClaim.objects.create(incident=incident, subject_id=fact.subject_id, field='need_by', value='2026-10-18T12:00:00',
                                      source=fact.source, observed_at=fact.observed_at, basis='naive time')


def test_snapshot_is_deeply_immutable(incident):
    snapshot = build_snapshot(incident.id)
    with pytest.raises(TypeError):
        snapshot.policy.unit_aliases['boxes'] = 'bottle'
    demand = next(claim for claim in snapshot.claims if claim.field == 'daily_demand')
    with pytest.raises(TypeError):
        demand.value[0]['units'] = 999


def test_explicit_unknown_fact_does_not_require_invented_evidence(incident):
    fact = CanonicalFact.objects.filter(incident=incident, field='on_hand').first()
    fact.value, fact.status, fact.claim, fact.source, fact.observed_at = None, 'UNKNOWN', None, None, None
    fact.basis = 'No current count is available'
    fact.save()
    projected = next(batch for batch in build_snapshot(incident.id).batches if batch.id == fact.subject_id).on_hand
    assert projected.value is None
    assert projected.status == 'UNKNOWN'
    assert projected.evidence_id is None


@pytest.mark.parametrize('string_id', [False, True])
def test_seed_and_multi_record_mutations_have_one_revision(incident, string_id):
    assert incident.revision == 1
    from healthmesh.db_models.core import incident_mutation
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    before = build_snapshot(incident.id)
    batch = before.batches[0]
    with incident_mutation(str(incident.id) if string_id else incident.id):
        for field, value in [('on_hand', 80), ('reserved', 25)]:
            InventoryClaim.objects.create(incident=incident, subject_id=batch.id, field=field, value=value,
                                          source=source, observed_at=source.observed_at, basis='one evidence change')
        with pytest.raises(ValidationError):
            build_snapshot(incident.id)
    assert build_snapshot(incident.id).revision == before.revision + 1
    accepted = InventoryClaim.objects.filter(incident=incident).count()
    with pytest.raises(RuntimeError):
        with incident_mutation(incident.id):
            InventoryClaim.objects.create(incident=incident, subject_id=batch.id, field='on_hand', value=90,
                                          source=source, observed_at=source.observed_at, basis='rolled back')
            raise RuntimeError('abort the entire evidence change')
    assert InventoryClaim.objects.filter(incident=incident).count() == accepted
    assert build_snapshot(incident.id).revision == before.revision + 1


@pytest.mark.parametrize(('field', 'value'), [('reserved', -1), ('reserve_accounting', 'includes_protected_reserve')])
def test_database_constraints_guard_inventory(incident, field, value):
    fact = CanonicalFact.objects.get(incident=incident, field=field, subject_id=build_snapshot(incident.id).batches[0].id)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=CanonicalFact).filter(pk=fact.pk).update(value=value)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=InventoryClaim).filter(pk=fact.claim_id).update(value=value)
