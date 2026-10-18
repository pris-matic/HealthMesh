"""Human responses are new evidence, with simulated arrival and explicit scope."""
import json
from datetime import timedelta
from threading import Barrier, Lock, Thread
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client

from healthmesh.domain.snapshot import build_snapshot
from healthmesh.evidence.conflicts import detect_conflicts
from healthmesh.models import (CanonicalFact, EvidenceConflict, Incident, InventoryClaim,
                               SourceDocument, SourceSpan, VerificationTask)
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident


pytestmark = pytest.mark.django_db


@pytest.fixture
def case(django_user_model):
    coordinator = django_user_model.objects.create_user('response-coordinator')
    coordinator.groups.add(Group.objects.get_or_create(name='coordinator')[0])
    reviewer = django_user_model.objects.create_user('response-reviewer')
    reviewer.groups.add(Group.objects.get_or_create(name='reviewer')[0])
    incident = create_incident(load_scenario('storm-supply-01'), coordinator)
    snapshot = build_snapshot(incident.id)
    brook = next(batch for batch in snapshot.batches if batch.facility_name == 'Brook')
    old = CanonicalFact.objects.get(incident=incident, subject_id=brook.id, field='on_hand').claim
    note = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    span = next(span for span in SourceSpan.objects.filter(source=note)
                if 'counted 80 boxes' in span.text)
    candidate = InventoryClaim.objects.create(incident=incident, subject_id=brook.id,
        field='on_hand', value=80, source=note, span=span, observed_at=note.observed_at,
        status='OBSERVED_UNVERIFIED', basis='Unsigned synthetic note')
    conflict = detect_conflicts(incident.id)[0]
    incident.refresh_from_db()
    task = VerificationTask.objects.create(
        incident=incident, created_by=coordinator, subject_id=brook.id, field='on_hand',
        selected_snapshot_revision=incident.revision, selected_at=incident.simulated_now,
        due_at=incident.simulated_now + timedelta(minutes=10), staff_minutes=3,
        response_delay_minutes=10, evidence_ids=[str(old.id), str(candidate.id)])
    return SimpleNamespace(incident=incident, coordinator=coordinator, reviewer=reviewer,
                           brook=brook, old=old, candidate=candidate, conflict=conflict,
                           task=task)


def test_timely_brook_recount_appends_exact_human_evidence_and_resolves_selected_conflict(case):
    """Dropping any manual source, span, claim, or one-revision boundary breaks this."""
    from healthmesh.models import VerificationResponse
    from healthmesh.workflow.verification import VerificationAnswer, respond_to_verification

    before = case.incident.revision
    as_of = case.incident.simulated_now
    revision = respond_to_verification(case.task.id, VerificationAnswer(
        status='CONFIRMED', value=90, unit='box',
        claimed_source='SYNTHETIC Brook physical shelf recount',
        expected_revision=before), case.coordinator)

    case.incident.refresh_from_db()
    response = VerificationResponse.objects.get(task=case.task)
    manual = InventoryClaim.objects.get(pk=revision.claim_id)
    canonical = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='on_hand')
    conflict = EvidenceConflict.objects.get(pk=case.conflict.id)
    assert (case.incident.revision, revision.revision) == (before + 1, before + 1)
    assert case.incident.simulated_now == as_of + timedelta(minutes=10)
    assert revision.simulated_at == case.incident.simulated_now
    assert (response.status, response.timely, response.value, response.unit) == ('CONFIRMED', True, 90, 'box')
    assert response.claim_id == manual.id and response.actor_id == case.coordinator.id
    assert (manual.value, manual.status, manual.subject_id, manual.field) == (
        90, 'HUMAN_VERIFIED', case.brook.id, 'on_hand')
    assert manual.source.kind == 'MANUAL' and manual.source.verified is True
    assert manual.span.source_id == manual.source_id
    assert manual.span.text == 'on_hand=90'
    assert manual.span.text == manual.source.decoded_text[manual.span.start:manual.span.end]
    assert (canonical.value, canonical.status, canonical.claim_id, canonical.source_id) == (
        90, 'HUMAN_VERIFIED', manual.id, manual.source_id)
    assert (conflict.state, conflict.resolution_id) == ('RESOLVED', manual.id)
    assert set(conflict.claim_ids) == {str(case.old.id), str(case.candidate.id), str(manual.id)}
    assert InventoryClaim.objects.get(pk=case.old.id).value == 95
    assert InventoryClaim.objects.get(pk=case.candidate.id).status == 'OBSERVED_UNVERIFIED'
    assert InventoryClaim.objects.get(pk=case.old.id).span.text == '"on_hand": 95'
    assert 'counted 80 boxes' in InventoryClaim.objects.get(pk=case.candidate.id).span.text
    snapshot = build_snapshot(case.incident.id)
    after_brook = next(batch for batch in snapshot.batches if batch.id == case.brook.id)
    assert (after_brook.on_hand.value, after_brook.on_hand.status,
            after_brook.on_hand.evidence_id) == (90, 'HUMAN_VERIFIED', manual.id)
    assert {claim.value for claim in snapshot.claims if claim.subject_id == case.brook.id
            and claim.field == 'on_hand'} == {95, 80, 90}


@pytest.mark.parametrize('status', ['UNRESOLVED', 'REJECTED'])
def test_nonconfirmed_response_advances_time_without_promoting_evidence(case, status):
    from healthmesh.models import VerificationResponse
    from healthmesh.workflow.verification import VerificationAnswer, respond_to_verification

    before = case.incident.revision
    claim_count = InventoryClaim.objects.count()
    source_count = SourceDocument.objects.count()
    response = respond_to_verification(case.task.id, VerificationAnswer(
        status=status, claimed_source='SYNTHETIC Brook coordinator unable to verify',
        expected_revision=before), case.coordinator)
    case.incident.refresh_from_db()
    canonical = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='on_hand')
    assert (case.incident.revision, response.revision) == (before + 1, before + 1)
    assert case.incident.simulated_now == case.task.due_at
    assert response.claim_id is None
    assert VerificationResponse.objects.get(task=case.task).claim_id is None
    assert InventoryClaim.objects.count() == claim_count
    assert SourceDocument.objects.count() == source_count
    assert (canonical.value, canonical.claim_id) == (95, case.old.id)
    assert EvidenceConflict.objects.get(pk=case.conflict.id).state == 'OPEN'


def test_late_confirmation_records_answer_without_verifying_stock(case):
    from healthmesh.models import VerificationResponse
    from healthmesh.workflow.clock import advance_simulated_clock
    from healthmesh.workflow.verification import VerificationAnswer, respond_to_verification

    advance_simulated_clock(case.incident.id, 24 * 60 + 1, case.coordinator)
    case.incident.refresh_from_db()
    before = case.incident.revision
    late_at = case.incident.simulated_now
    claim_count = InventoryClaim.objects.count()
    response = respond_to_verification(case.task.id, VerificationAnswer(
        status='CONFIRMED', value=90, unit='box',
        claimed_source='SYNTHETIC Brook late shelf recount',
        expected_revision=before), case.coordinator)
    case.incident.refresh_from_db()
    recorded = VerificationResponse.objects.get(task=case.task)
    canonical = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='on_hand')
    assert case.incident.simulated_now == late_at
    assert case.incident.revision == before + 1
    assert (recorded.status, recorded.timely, recorded.value, recorded.claim_id) == (
        'CONFIRMED', False, 90, None)
    assert response.claim_id is None and response.outcome == 'LATE'
    assert InventoryClaim.objects.count() == claim_count
    assert (canonical.value, canonical.claim_id) == (95, case.old.id)
    assert EvidenceConflict.objects.get(pk=case.conflict.id).state == 'OPEN'


def test_clock_is_monotonic_and_zero_delay_is_revision_neutral(case):
    from healthmesh.workflow.clock import advance_simulated_clock
    from healthmesh.workflow.verification import VerificationError

    before = case.incident.revision
    as_of = case.incident.simulated_now
    unchanged = advance_simulated_clock(case.incident.id, 0, case.coordinator)
    assert (unchanged.revision, unchanged.simulated_at) == (before, as_of)
    advanced = advance_simulated_clock(case.incident.id, 24 * 60, case.coordinator)
    assert (advanced.revision, advanced.simulated_at) == (
        before + 1, as_of + timedelta(days=1))
    assert build_snapshot(case.incident.id).as_of == as_of + timedelta(days=1)
    with pytest.raises(VerificationError) as exc:
        advance_simulated_clock(case.incident.id, -1, case.coordinator)
    assert exc.value.code == 'invalid_time'
    case.incident.refresh_from_db()
    assert (case.incident.revision, case.incident.simulated_now) == (
        before + 1, as_of + timedelta(days=1))


@pytest.mark.parametrize('answer_kind,actor_name,expected_code', [
    ('missing_source', 'coordinator', 'invalid_source'),
    ('wrong_unit', 'coordinator', 'unit_mismatch'),
    ('wrong_value', 'coordinator', 'invalid_value'),
    ('stale', 'coordinator', 'stale_snapshot'),
    ('wrong_role', 'reviewer', 'forbidden'),
])
def test_invalid_answer_rolls_back_without_advancing_time(case, answer_kind, actor_name, expected_code):
    from healthmesh.models import VerificationResponse
    from healthmesh.workflow.verification import VerificationAnswer, VerificationError, respond_to_verification

    before = case.incident.revision
    as_of = case.incident.simulated_now
    source_count = SourceDocument.objects.count()
    claim_count = InventoryClaim.objects.count()
    answer = VerificationAnswer(status='CONFIRMED', value=90, unit='box',
        claimed_source='SYNTHETIC Brook physical recount', expected_revision=before)
    changes = {
        'missing_source': {'claimed_source': ''},
        'wrong_unit': {'unit': 'piece'},
        'wrong_value': {'value': 90.5},
        'stale': {'expected_revision': before - 1},
        'wrong_role': {},
    }
    answer = VerificationAnswer(**{**answer.__dict__, **changes[answer_kind]})
    with pytest.raises(VerificationError) as exc:
        respond_to_verification(case.task.id, answer, getattr(case, actor_name))
    assert exc.value.code == expected_code
    assert exc.value.http_status in {400, 403, 409}
    case.incident.refresh_from_db()
    assert (case.incident.revision, case.incident.simulated_now) == (before, as_of)
    assert VerificationResponse.objects.count() == 0
    assert SourceDocument.objects.count() == source_count
    assert InventoryClaim.objects.count() == claim_count


def test_duplicate_response_cannot_mutate_append_only_history(case):
    from healthmesh.models import VerificationResponse
    from healthmesh.workflow.verification import VerificationAnswer, VerificationError, respond_to_verification

    answer = VerificationAnswer(status='CONFIRMED', value=90, unit='box',
        claimed_source='SYNTHETIC Brook physical recount', expected_revision=case.incident.revision)
    respond_to_verification(case.task.id, answer, case.coordinator)
    case.incident.refresh_from_db()
    before = case.incident.revision
    response = VerificationResponse.objects.get(task=case.task)
    with pytest.raises(VerificationError) as exc:
        respond_to_verification(case.task.id, answer, case.coordinator)
    assert exc.value.code == 'already_answered'
    response.status = 'REJECTED'
    with pytest.raises(ValidationError):
        response.save()
    with pytest.raises(ValidationError):
        response.delete()
    case.incident.refresh_from_db()
    assert case.incident.revision == before
    assert VerificationResponse.objects.get(task=case.task).status == 'CONFIRMED'


def test_cedar_status_confirmation_creates_and_resolves_its_own_conflict(case):
    from healthmesh.workflow.verification import VerificationAnswer, respond_to_verification

    route = next(row for row in build_snapshot(case.incident.id).routes if row.origin_name == 'Cedar')
    task = VerificationTask.objects.create(
        incident=case.incident, created_by=case.coordinator, subject_id=route.id,
        field='route_status', selected_snapshot_revision=case.incident.revision,
        selected_at=case.incident.simulated_now,
        due_at=case.incident.simulated_now + timedelta(minutes=20), staff_minutes=5,
        response_delay_minutes=20, evidence_ids=[str(route.status.evidence_id)])
    before = case.incident.revision
    revision = respond_to_verification(task.id, VerificationAnswer(
        status='CONFIRMED', value='CLOSED',
        claimed_source='SYNTHETIC Cedar route inspection', expected_revision=before), case.coordinator)
    case.incident.refresh_from_db()
    canonical = CanonicalFact.objects.get(incident=case.incident, subject_id=route.id,
                                          field='route_status')
    conflict = EvidenceConflict.objects.get(pk=revision.conflict_id)
    assert case.incident.revision == before + 1
    assert case.incident.simulated_now == task.due_at
    assert (canonical.value, canonical.status, canonical.claim_id) == (
        'CLOSED', 'HUMAN_VERIFIED', revision.claim_id)
    assert (conflict.field, conflict.state, conflict.resolution_id) == (
        'route_status', 'RESOLVED', revision.claim_id)
    assert EvidenceConflict.objects.get(pk=case.conflict.id).state == 'OPEN'


def test_coordinator_response_api_returns_new_revision_and_derived_ids(case):
    client = Client()
    client.force_login(case.coordinator)
    answer = {'status': 'CONFIRMED', 'value': 90, 'unit': 'box',
              'claimed_source': 'SYNTHETIC Brook physical recount',
              'expected_revision': case.incident.revision}
    result = client.post(f'/api/v1/verification-tasks/{case.task.id}/respond',
                         json.dumps(answer), content_type='application/json')
    assert result.status_code == 201, result.content
    payload = result.json()
    assert payload['incident_id'] == str(case.incident.id)
    assert payload['new_revision'] == case.incident.revision + 1
    assert payload['simulated_at'] == case.task.due_at.isoformat()
    assert payload['outcome'] == 'CONFIRMED'
    assert InventoryClaim.objects.get(pk=payload['claim_id']).value == 90
    assert EvidenceConflict.objects.get(pk=payload['conflict_id']).state == 'RESOLVED'
    assert payload['new_plan_run_id'] is None


@pytest.mark.parametrize('problem,expected_code', [
    ('wrong_role', 'forbidden'),
    ('missing_source', 'invalid_source'),
    ('wrong_unit', 'unit_mismatch'),
    ('stale_revision', 'stale_snapshot'),
])
def test_response_api_rejects_invalid_actions_with_structured_error(case, problem, expected_code):
    from healthmesh.models import VerificationResponse

    client = Client()
    client.force_login(case.reviewer if problem == 'wrong_role' else case.coordinator)
    answer = {'status': 'CONFIRMED', 'value': 90, 'unit': 'box',
              'claimed_source': 'SYNTHETIC Brook physical recount',
              'expected_revision': case.incident.revision}
    if problem == 'missing_source':
        answer['claimed_source'] = ''
    elif problem == 'wrong_unit':
        answer['unit'] = 'piece'
    elif problem == 'stale_revision':
        answer['expected_revision'] -= 1
    result = client.post(f'/api/v1/verification-tasks/{case.task.id}/respond',
                         json.dumps(answer), content_type='application/json')
    assert result.status_code in {400, 403, 409}, result.content
    assert set(result.json()) == {'code', 'message', 'field_errors', 'request_id'}
    assert result.json()['code'] == expected_code
    assert not VerificationResponse.objects.filter(task=case.task).exists()
    case.incident.refresh_from_db()
    assert case.incident.revision == answer['expected_revision'] if problem != 'stale_revision' else case.incident.revision == answer['expected_revision'] + 1


def test_selected_task_exposes_authoritative_unit_and_simulated_deadline(case):
    client = Client()
    client.force_login(case.coordinator)
    result = client.get(f'/api/v1/incidents/{case.incident.id}/verification-tasks')
    assert result.status_code == 200
    selected = result.json()['selected_task']
    assert selected['id'] == str(case.task.id)
    assert selected['unit'] == 'box'
    assert selected['need_by'] == '2026-10-18T12:00:00+00:00'


def test_independent_claim_review_appends_human_attestation_without_silently_resolving_conflict(case):
    from healthmesh.models import HumanAttestation

    client = Client()
    client.force_login(case.coordinator)
    answer = {'value': 90, 'unit': 'box',
              'claimed_source': 'SYNTHETIC Brook independent shelf recount',
              'expected_revision': case.incident.revision}
    result = client.post(f'/api/v1/claims/{case.candidate.id}/review',
                         json.dumps(answer), content_type='application/json')
    assert result.status_code == 201, result.content
    payload = result.json()
    assert payload['new_revision'] == case.incident.revision + 1
    attestation = HumanAttestation.objects.get(pk=payload['attestation_id'])
    assert attestation.actor_id == case.coordinator.id
    assert attestation.target_claim_id == case.candidate.id
    assert attestation.target_conflict_id is None
    assert attestation.claim_id == payload['claim_id']
    assert attestation.claimed_source == answer['claimed_source']
    assert InventoryClaim.objects.get(pk=case.candidate.id).status == 'OBSERVED_UNVERIFIED'
    assert InventoryClaim.objects.get(pk=payload['claim_id']).status == 'HUMAN_VERIFIED'
    assert EvidenceConflict.objects.get(pk=case.conflict.id).state == 'OPEN'
    brook = next(batch for batch in build_snapshot(case.incident.id).batches
                 if batch.id == case.brook.id)
    assert (brook.on_hand.value, brook.on_hand.status) == (90, 'CONFLICTED')


def test_independent_conflict_resolution_requires_fresh_human_value_and_closes_only_target(case):
    from healthmesh.models import HumanAttestation

    client = Client()
    client.force_login(case.coordinator)
    answer = {'value': 90, 'unit': 'box',
              'claimed_source': 'SYNTHETIC Brook independent shelf recount',
              'expected_revision': case.incident.revision}
    result = client.post(f'/api/v1/conflicts/{case.conflict.id}/resolve',
                         json.dumps(answer), content_type='application/json')
    assert result.status_code == 201, result.content
    payload = result.json()
    attestation = HumanAttestation.objects.get(pk=payload['attestation_id'])
    assert attestation.target_claim_id is None
    assert attestation.target_conflict_id == case.conflict.id
    assert attestation.actor_id == case.coordinator.id
    assert InventoryClaim.objects.get(pk=case.candidate.id).status == 'OBSERVED_UNVERIFIED'
    assert InventoryClaim.objects.get(pk=payload['claim_id']).status == 'HUMAN_VERIFIED'
    conflict = EvidenceConflict.objects.get(pk=case.conflict.id)
    assert conflict.resolution_id == payload['claim_id']
    assert conflict.state == 'RESOLVED'
    assert set(conflict.claim_ids) == {str(case.old.id), str(case.candidate.id), payload['claim_id']}
