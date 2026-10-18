"""Atomically retain originals, sanitized spans and unverified candidate claims."""
import hashlib
from pathlib import PurePath
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction

from healthmesh.db_models.core import incident_mutation
from healthmesh.models import Facility, Incident, InventoryClaim, SourceDocument, SourceSpan, StockBatch
from .parsers import parse_source


@transaction.atomic
def ingest_source(incident_id, facility_id, filename, content, actor) -> SourceDocument:
    if (not actor or not actor.is_authenticated or not actor.is_active
            or not actor.groups.filter(name='coordinator').exists()):
        raise PermissionDenied('An active coordinator is required.')
    try:
        incident_id, facility_id = UUID(str(incident_id)), UUID(str(facility_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({'scope': 'Valid incident and facility UUIDs are required.'})
    incident = Incident.objects.select_for_update().get(pk=incident_id, synthetic=True)
    facility = Facility.objects.get(pk=facility_id, incident=incident)
    if not isinstance(content, bytes) or not isinstance(filename, str):
        raise ValidationError({'file': 'Source bytes and a filename are required.'})
    parsed = parse_source(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    duplicate = SourceDocument.objects.filter(incident=incident, facility=facility, sha256=digest).first()
    if duplicate:
        duplicate.duplicate = True
        return duplicate
    batches = []
    for row in parsed.rows:
        if row['observed_at'] > incident.simulated_now:
            raise ValidationError({'file': 'Future observations are not accepted.'})
        try:
            batches.append(StockBatch.objects.get(incident=incident, facility=facility,
                           product__code=row['product_code'], batch_code=row['batch_code']))
        except ObjectDoesNotExist:
            raise ValidationError({'file': 'CSV batch must already belong to this fictional facility and product.'})
    observed_at = max((r['observed_at'] for r in parsed.rows), default=incident.simulated_now)
    # Original blobs are retained only in the private database, never under
    # static/media roots, fixtures or an unauthenticated source download route.
    with incident_mutation(incident.id):
        source = SourceDocument.objects.create(incident=incident, facility=facility, kind=parsed.kind,
            observed_at=observed_at, filename=PurePath(filename).name[:200], original_blob=content,
            decoded_text=parsed.amd_text, sha256=digest, verified=False)
        for index, draft in enumerate(parsed.spans):
            span = SourceSpan.objects.create(incident=incident, source=source, start=draft.start, end=draft.end)
            if parsed.kind == 'CSV':
                row = parsed.rows[index]
                for field in ('on_hand', 'reserved', 'protected_reserve', 'reserve_accounting'):
                    InventoryClaim.objects.create(incident=incident, subject_id=batches[index].id,
                        source=source, span=span, field=field, value=row[field], observed_at=row['observed_at'],
                        status='OBSERVED_UNVERIFIED', basis='Unsigned synthetic CSV observation; human verification required')
        if parsed.kind == 'CSV':
            from healthmesh.evidence.conflicts import detect_conflicts
            detect_conflicts(incident.id)
    source.duplicate = False
    return source
