"""Read-only, sanitized provenance view for synthetic incident evidence."""
from datetime import timedelta
import time
from uuid import UUID

from django.db import OperationalError, connection, transaction
from django.http import JsonResponse

from healthmesh.models import (CanonicalFact, EvidenceConflict, Incident, InventoryClaim,
                               StockBatch, SupplyRequest, VerificationTask)
from healthmesh.domain.snapshot import build_snapshot
from healthmesh.planner.service import plan
from healthmesh.workflow.auth import api, body, error
from .ranking import rank_verifications


@api(['GET'])
def evidence(request, incident_id):
    # SQLite's shared-cache test database may report a lock instead of waiting
    # for the competing writer. Release the read transaction and retry a fresh
    # revision; never combine results from two attempts.
    for attempt in range(3):
        try:
            return _evidence_response(incident_id)
        except OperationalError as exc:
            if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                raise
            if attempt == 2:
                return error('temporarily_unavailable', 'Evidence is busy. Retry this read.', 503)
            time.sleep(.05 * (attempt + 1))


@transaction.atomic
def _evidence_response(incident_id):
    # Every incident evidence writer locks this row before changing claims or
    # conflicts. Hold it through all reads so one response describes one revision.
    incident = Incident.objects.select_for_update().filter(pk=incident_id, synthetic=True).first()
    if incident is None:
        return error('not_found', 'Incident not found.', 404)
    freshness = timedelta(hours=incident.policy['freshness_hours'])
    conflicts = list(EvidenceConflict.objects.filter(incident=incident).order_by('field_key', 'created_at', 'id'))
    conflicted = {(row.subject_id, row.field) for row in conflicts if row.state == 'OPEN'}
    claims = []
    for claim in (InventoryClaim.objects.filter(incident=incident)
                  .select_related('source', 'span', 'extraction_run').order_by('subject_id', 'field', 'observed_at', 'id')):
        source = claim.source
        span = claim.span
        claims.append({
            'id': str(claim.id), 'subject_id': str(claim.subject_id), 'field': claim.field,
            'value': claim.value, 'status': claim.status,
            'field_status': 'CONFLICTED' if (claim.subject_id, claim.field) in conflicted else claim.status,
            'freshness': 'STALE' if incident.simulated_now - claim.observed_at > freshness else 'FRESH',
            'observed_at': claim.observed_at.isoformat(),
            'source': {'id': str(source.id), 'kind': source.kind,
                       'observed_at': source.observed_at.isoformat(), 'verified': source.verified},
            'span': {'id': str(span.id), 'start': span.start, 'end': span.end,
                     'quote': source.decoded_text[span.start:span.end]} if span else None,
            'receipt_id': str(claim.extraction_run.receipt_id) if claim.extraction_run_id else None,
        })
    return JsonResponse({
        'incident_id': str(incident.id), 'revision': incident.revision,
        'as_of': incident.simulated_now.isoformat(), 'claims': claims,
        'conflicts': [{
            'id': str(row.id), 'field_key': row.field_key, 'subject_id': str(row.subject_id),
            'field': row.field, 'claim_ids': row.claim_ids, 'conflict_type': row.conflict_type,
            'state': row.state, 'resolution_id': str(row.resolution_id) if row.resolution_id else None,
            'created_at': row.created_at.isoformat(),
        } for row in conflicts],
    })


def _task_payload(task):
    batch = (StockBatch.objects.select_related('product').get(
        pk=task.subject_id, incident_id=task.incident_id)
        if task.field == 'on_hand' else None)
    request = SupplyRequest.objects.filter(incident_id=task.incident_id).first()
    deadline = (CanonicalFact.objects.filter(incident_id=task.incident_id,
        subject_id=request.id, field='need_by').first() if request else None)
    return {'id': str(task.id), 'incident_id': str(task.incident_id),
            'subject_id': str(task.subject_id), 'field': task.field,
            'selected_snapshot_revision': task.selected_snapshot_revision,
            'staff_minutes': task.staff_minutes, 'response_delay_minutes': task.response_delay_minutes,
            'selected_at': task.selected_at.isoformat(), 'due_at': task.due_at.isoformat(),
            'need_by': deadline.value if deadline and deadline.status in {
                'HUMAN_VERIFIED', 'DERIVED_FROM_VERIFIED'} else None,
            'unit': batch.product.base_unit if batch else None,
            'evidence_ids': task.evidence_ids}


def _task_fallback(task):
    """Selection is quota free; AMD wording is an explicit later action."""
    from healthmesh.ai_gateway.grounding import (GroundingUnavailable,
                                                 context_for_task, fallback_text)
    try:
        context = context_for_task(task)
        text = fallback_text(context, 'draft')
        span_ids = [str(line.span.id) for line in context.lines]
    except GroundingUnavailable:
        text = 'Please confirm the selected fact from a named human source?'
        span_ids = []
    return {'status': 'UNAVAILABLE', 'source': 'DETERMINISTIC_FALLBACK',
            'text': text, 'cited_span_ids': span_ids, 'receipt_id': None, 'receipt': None}


def verification_tasks(request, incident_id):
    if request.method == 'GET':
        return list_verification_tasks(request, incident_id)
    if request.method == 'POST':
        return create_verification_task(request, incident_id)
    return error('method_not_allowed', 'Method not allowed.', 405)


@api(['GET'])
def list_verification_tasks(request, incident_id):
    incident = Incident.objects.filter(pk=incident_id, synthetic=True).first()
    if incident is None:
        return error('not_found', 'Incident not found.', 404)
    snapshot = build_snapshot(incident_id)
    selected = list(VerificationTask.objects.filter(incident_id=incident_id).order_by('created_at', 'id'))
    budget = snapshot.policy.verification_budget.model_copy(update={
        'questions': max(0, snapshot.policy.verification_budget.questions - len(selected)),
        'staff_minutes': max(0, snapshot.policy.verification_budget.staff_minutes - sum(row.staff_minutes for row in selected))})
    ranking = rank_verifications(snapshot, plan(snapshot), budget)
    return JsonResponse({**ranking.model_dump(mode='json'),
                         'selected_task': _task_payload(selected[-1]) if selected else None})


@api(['POST'], roles=('coordinator',))
def create_verification_task(request, incident_id):
    data = body(request)
    try:
        subject_id = UUID(data['subject_id'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return error('invalid_request', 'A valid subject_id is required.', 400)
    field = data.get('field')
    expected = data.get('expected_revision')
    if field not in {'on_hand', 'route_status'} or type(expected) is not int or expected < 0:
        return error('invalid_request', 'Field and expected_revision are required.', 400)
    # SQLite cannot enforce select_for_update. A competing selector can win
    # after this request read the budget, so retry in a new transaction and
    # recompute the budget rather than returning an unhandled database lock.
    for attempt in range(4):
        try:
            return _create_verification_task_once(request, incident_id, subject_id, field, expected)
        except OperationalError as exc:
            if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
                raise
            if attempt == 3:
                return error('temporarily_unavailable', 'Verification is busy. Retry selection.', 503)
            time.sleep(.05 * (attempt + 1))


@transaction.atomic
def _create_verification_task_once(request, incident_id, subject_id, field, expected):
    incident = Incident.objects.select_for_update().filter(pk=incident_id, synthetic=True).first()
    if incident is None:
        return error('not_found', 'Incident not found.', 404)
    existing = list(VerificationTask.objects.filter(incident=incident).order_by('created_at', 'id'))
    replay = next((row for row in existing if row.selected_snapshot_revision == expected
                   and row.subject_id == subject_id and row.field == field), None)
    if replay is not None:
        return JsonResponse({'selected_task': _task_payload(replay), 'new_revision': None,
                             'grounded_wording': _task_fallback(replay)}, status=200)
    if incident.revision != expected:
        return error('stale_snapshot', 'Incident evidence changed; refresh verification options.', 409)
    snapshot = build_snapshot(incident.id)
    policy_budget = snapshot.policy.verification_budget
    budget = policy_budget.model_copy(update={
        'questions': max(0, policy_budget.questions - len(existing)),
        'staff_minutes': max(0, policy_budget.staff_minutes - sum(row.staff_minutes for row in existing))})
    ranked = rank_verifications(snapshot, plan(snapshot), budget)
    selected = next((row for row in ranked.tasks if row.subject_id == subject_id and row.field == field), None)
    if selected is None:
        return error('NO_POSITIVE_OPTIONAL_QUERY', 'No budgeted decision-changing question is available.', 409)
    task = VerificationTask.objects.create(
        incident=incident, created_by=request.user, subject_id=subject_id, field=field,
        selected_snapshot_revision=snapshot.revision, selected_at=snapshot.as_of,
        due_at=snapshot.as_of + timedelta(minutes=selected.response_delay_minutes),
        staff_minutes=selected.staff_minutes, response_delay_minutes=selected.response_delay_minutes,
        evidence_ids=[str(value) for value in selected.evidence_ids])
    return JsonResponse({'selected_task': _task_payload(task), 'new_revision': None,
                         'grounded_wording': _task_fallback(task)}, status=201)
