"""One human answer creates at most one new verified fact and revision."""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.db import IntegrityError, OperationalError, connection, transaction

from healthmesh.db_models.core import incident_mutation
from healthmesh.evidence.conflicts import ensure_selected_conflict, resolve_selected_conflict
from healthmesh.models import (CanonicalFact, EvidenceConflict, HumanAttestation, Incident,
                               InventoryClaim, RouteObservation,
                               SourceDocument, SourceSpan, StockBatch, SupplyRequest,
                               VerificationResponse, VerificationTask)
from .clock import EvidenceRevision, move_simulated_time, require_coordinator


@dataclass(frozen=True)
class VerificationAnswer:
    status: str
    value: object = None
    unit: str | None = None
    claimed_source: str = ''
    expected_revision: int | None = None


class VerificationError(ValueError):
    def __init__(self, code: str, message: str, http_status: int,
                 field_errors: dict | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.field_errors = field_errors or {}


def _validated_answer(task: VerificationTask, answer: VerificationAnswer):
    if answer.status not in {'CONFIRMED', 'REJECTED', 'UNRESOLVED'}:
        raise VerificationError('invalid_answer', 'Choose CONFIRMED, REJECTED, or UNRESOLVED.', 400,
                                {'status': 'Invalid response status.'})
    if (not isinstance(answer.claimed_source, str) or
            not answer.claimed_source.strip() or len(answer.claimed_source.strip()) > 200 or
            any(ord(char) < 32 for char in answer.claimed_source)):
        raise VerificationError('invalid_source', 'A named synthetic human source is required.', 400,
                                {'claimed_source': 'Enter one source label of at most 200 characters.'})
    if answer.status != 'CONFIRMED':
        if answer.value is not None or answer.unit not in {None, ''}:
            raise VerificationError('invalid_answer', 'Only a confirmation can include a value and unit.', 400,
                                    {'value': 'Leave value and unit empty.'})
        return None, '', None
    if task.field == 'on_hand':
        batch = StockBatch.objects.select_related('product', 'facility').get(
            pk=task.subject_id, incident_id=task.incident_id)
        if type(answer.value) is not int or answer.value < 0:
            raise VerificationError('invalid_value', 'Count must be a nonnegative whole number.', 400,
                                    {'value': 'Use a nonnegative whole number.'})
        if answer.unit != batch.product.base_unit:
            raise VerificationError('unit_mismatch', 'Count unit must match the selected batch.', 400,
                                    {'unit': f'Use {batch.product.base_unit}.'})
        return answer.value, answer.unit, batch.facility
    route = RouteObservation.objects.select_related('origin').get(
        pk=task.subject_id, incident_id=task.incident_id)
    if answer.value not in {'OPEN', 'CLOSED'} or type(answer.value) is not str:
        raise VerificationError('invalid_value', 'Confirm OPEN or CLOSED route status.', 400,
                                {'value': 'Use OPEN or CLOSED.'})
    if answer.unit not in {None, ''}:
        raise VerificationError('unit_mismatch', 'Route status has no unit.', 400,
                                {'unit': 'Leave unit empty.'})
    return answer.value, '', route.origin


def _need_by(incident: Incident) -> datetime | None:
    request = SupplyRequest.objects.filter(incident=incident).first()
    if request is None:
        return None
    deadline = CanonicalFact.objects.filter(incident=incident, subject_id=request.id,
                                            field='need_by').first()
    if deadline is None or deadline.status not in {'HUMAN_VERIFIED', 'DERIVED_FROM_VERIFIED'}:
        return None
    return datetime.fromisoformat(deadline.value)


def _manual_evidence(task, incident, actor, value, unit, source_label, facility,
                     resolve_conflict=True):
    field_text = f'{task.field}={value}'
    text = f'SYNTHETIC {source_label} | {field_text}'
    if unit:
        text += f' {unit}'
    text += f' | attested at {incident.simulated_now.isoformat()} | event {uuid4()}'
    source = SourceDocument.objects.create(
        incident=incident, facility=facility, kind='MANUAL', verified=True,
        observed_at=incident.simulated_now, filename='',
        original_blob=text.encode('utf-8'), decoded_text=text)
    start = text.index(field_text)
    span = SourceSpan.objects.create(incident=incident, source=source,
                                     start=start, end=start + len(field_text))
    claim = InventoryClaim.objects.create(
        incident=incident, subject_id=task.subject_id, field=task.field, value=value,
        source=source, span=span, observed_at=incident.simulated_now,
        status='HUMAN_VERIFIED', basis=f'Coordinator attestation from {source_label}')
    canonical = CanonicalFact.objects.get(incident=incident, subject_id=task.subject_id,
                                          field=task.field)
    canonical.value = value
    canonical.status = 'HUMAN_VERIFIED'
    canonical.source = source
    canonical.claim = claim
    canonical.observed_at = incident.simulated_now
    canonical.basis = f'Coordinator attestation by {actor.username}'
    canonical.save()
    conflict = (resolve_selected_conflict(incident.id, task.subject_id, task.field, claim)
                if resolve_conflict else
                ensure_selected_conflict(incident.id, task.subject_id, task.field))
    return claim, conflict


@transaction.atomic
def _respond_once(task_id: UUID, answer: VerificationAnswer, actor) -> EvidenceRevision:
    task = VerificationTask.objects.filter(pk=task_id).first()
    if task is None:
        raise VerificationError('not_found', 'Verification task not found.', 404)
    incident = Incident.objects.select_for_update().filter(pk=task.incident_id, synthetic=True).first()
    if incident is None:
        raise VerificationError('not_found', 'Incident not found.', 404)
    task = VerificationTask.objects.select_for_update().get(pk=task_id)
    if VerificationResponse.objects.filter(task=task).exists():
        raise VerificationError('already_answered', 'This question already has a response.', 409)
    if type(answer.expected_revision) is not int or answer.expected_revision != incident.revision:
        raise VerificationError('stale_snapshot', 'Incident evidence changed; refresh before responding.', 409,
                                {'expected_revision': 'Use the current incident revision.'})
    value, unit, facility = _validated_answer(task, answer)
    arrival = max(incident.simulated_now, task.due_at)
    deadline = _need_by(incident)
    timely = deadline is not None and arrival < deadline
    claim = conflict = None
    with incident_mutation(incident.id):
        move_simulated_time(incident, arrival)
        if answer.status == 'CONFIRMED' and timely:
            claim, conflict = _manual_evidence(task, incident, actor, value, unit,
                                                answer.claimed_source.strip(), facility)
        response = VerificationResponse.objects.create(
            incident=incident, task=task, actor=actor, status=answer.status,
            value=value, unit=unit, claimed_source=answer.claimed_source.strip(),
            observed_at=arrival, timely=timely, claim=claim)
    incident.refresh_from_db()
    return EvidenceRevision(incident_id=incident.id, revision=incident.revision,
                            simulated_at=incident.simulated_now, response_id=response.id,
                            claim_id=claim.id if claim else None,
                            conflict_id=conflict.id if conflict else None,
                            outcome=answer.status if timely else 'LATE')


def respond_to_verification(task_id: UUID, answer: VerificationAnswer,
                            actor) -> EvidenceRevision:
    require_coordinator(actor)
    for attempt in range(4):
        try:
            return _respond_once(task_id, answer, actor)
        except IntegrityError as exc:
            if VerificationResponse.objects.filter(task_id=task_id).exists():
                raise VerificationError('already_answered', 'This question already has a response.', 409) from exc
            raise
        except OperationalError as exc:
            if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                raise
            if attempt == 3:
                raise VerificationError('temporarily_unavailable', 'Verification is busy; retry.', 503) from exc
            time.sleep(.05 * (attempt + 1))
    raise RuntimeError('Verification retry loop exhausted.')


@transaction.atomic
def _attest_once(kind: str, target_id: UUID, answer: VerificationAnswer,
                 actor) -> EvidenceRevision:
    model = InventoryClaim if kind == 'CLAIM_REVIEW' else EvidenceConflict
    target = model.objects.filter(pk=target_id).first()
    if target is None:
        raise VerificationError('not_found', 'Evidence target not found.', 404)
    incident = Incident.objects.select_for_update().filter(pk=target.incident_id, synthetic=True).first()
    if incident is None:
        raise VerificationError('not_found', 'Incident not found.', 404)
    target = model.objects.select_for_update().get(pk=target_id)
    if kind == 'CONFLICT_RESOLUTION' and target.state != 'OPEN':
        raise VerificationError('conflict_not_open', 'Conflict is already resolved.', 409)
    if type(answer.expected_revision) is not int or answer.expected_revision != incident.revision:
        raise VerificationError('stale_snapshot', 'Incident evidence changed; refresh before attesting.', 409,
                                {'expected_revision': 'Use the current incident revision.'})
    deadline = _need_by(incident)
    if deadline is None or incident.simulated_now >= deadline:
        raise VerificationError('decision_expired', 'The simulated decision deadline has passed.', 409)
    value, unit, facility = _validated_answer(target, answer)
    with incident_mutation(incident.id):
        claim, conflict = _manual_evidence(
            target, incident, actor, value, unit, answer.claimed_source.strip(), facility,
            resolve_conflict=(kind == 'CONFLICT_RESOLUTION'))
        attestation = HumanAttestation.objects.create(
            incident=incident, actor=actor, action=kind,
            target_claim=target if kind == 'CLAIM_REVIEW' else None,
            target_conflict=target if kind == 'CONFLICT_RESOLUTION' else None,
            claim=claim, claimed_source=answer.claimed_source.strip(),
            observed_at=incident.simulated_now)
    incident.refresh_from_db()
    return EvidenceRevision(incident_id=incident.id, revision=incident.revision,
                            simulated_at=incident.simulated_now, attestation_id=attestation.id,
                            claim_id=claim.id,
                            conflict_id=conflict.id if kind == 'CONFLICT_RESOLUTION' and conflict else None,
                            outcome=kind)


def attest_to_evidence(kind: str, target_id: UUID, answer: VerificationAnswer,
                       actor) -> EvidenceRevision:
    require_coordinator(actor)
    if kind not in {'CLAIM_REVIEW', 'CONFLICT_RESOLUTION'}:
        raise ValueError('Unknown attestation action.')
    if answer.status != 'CONFIRMED':
        raise VerificationError('invalid_answer', 'A fresh confirmed human value is required.', 400,
                                {'status': 'Use CONFIRMED.'})
    for attempt in range(4):
        try:
            return _attest_once(kind, target_id, answer, actor)
        except OperationalError as exc:
            if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                raise
            if attempt == 3:
                raise VerificationError('temporarily_unavailable', 'Attestation is busy; retry.', 503) from exc
            time.sleep(.05 * (attempt + 1))
    raise RuntimeError('Attestation retry loop exhausted.')
