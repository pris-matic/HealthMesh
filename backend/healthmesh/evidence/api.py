"""Read-only, sanitized provenance view for synthetic incident evidence."""
from datetime import timedelta
import time

from django.db import OperationalError, connection, transaction
from django.http import JsonResponse

from healthmesh.models import EvidenceConflict, Incident, InventoryClaim
from healthmesh.workflow.auth import api, error


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
