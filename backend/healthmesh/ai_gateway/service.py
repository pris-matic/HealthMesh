"""Sanitized inference orchestration and unverified evidence persistence."""
from collections.abc import Sequence
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from healthmesh.db_models.core import incident_mutation
from healthmesh.sources.sanitizer import sanitize
from healthmesh.sources.spans import valid_span_offsets
from .adapter import AMDInferenceAdapter, UnavailableAMDAdapter
from .rate_gate import admitted_call
from .types import ExtractionResult, ReceiptMetadata
from .validation import validate_extraction


def get_amd_adapter() -> AMDInferenceAdapter:
    from .provider_event import TokenFactoryAMDAdapter, configured_for_token_factory
    if configured_for_token_factory():
        return TokenFactoryAMDAdapter()
    return UnavailableAMDAdapter()


def extract_claims(text: str, allowed_spans: Sequence, schema_version: str) -> ExtractionResult:
    """Sanitize before the provider boundary; timeout never creates evidence."""
    if (not isinstance(text, str) or len(text.encode('utf-8')) > AMDInferenceAdapter.max_input_bytes
            or len(allowed_spans) > AMDInferenceAdapter.max_spans or schema_version != 'v1'):
        return ExtractionResult(status='INVALID_OUTPUT')
    try:
        sanitized = sanitize(text)
    except ValidationError:
        return ExtractionResult(status='INVALID_OUTPUT')
    # A caller may supply only spans from the sanitized text view. Never pass
    # original-upload text hidden inside a span alongside a sanitized prompt.
    for span in allowed_spans:
        if (not valid_span_offsets(span, sanitized)
                or getattr(span, 'text', None) != sanitized[span.start:span.end]):
            return ExtractionResult(status='INVALID_OUTPUT')
    adapter = get_amd_adapter()
    if isinstance(adapter, UnavailableAMDAdapter):
        return adapter.extract_claims(sanitized, allowed_spans, schema_version)
    with admitted_call() as admitted:
        if not admitted:
            return ExtractionResult(status='UNAVAILABLE')
        try:
            return adapter.extract_claims(sanitized, allowed_spans, schema_version)
        except TimeoutError:
            return ExtractionResult(status='TIMEOUT')


def record_receipt(metadata: ReceiptMetadata):
    """Persist completed-call metadata without manufacturing timestamps or IDs.

    Only verified transport may supply genuine metadata. No prompt, headers,
    raw response or exception can be passed through this allowlisted contract.
    """
    from healthmesh.models import AIInferenceReceipt
    return AIInferenceReceipt.objects.create(**metadata.model_dump())


def _persistable_claims(validated):
    """Use exact integers for stock quantities; quarantine unsupported precision."""
    accepted, rejected = [], len(validated.quarantined)
    for claim in validated.accepted:
        value = claim.value
        if claim.field in {'on_hand', 'reserved'}:
            if (type(value) not in {int, Decimal} or value < 0 or value > 999_999_999
                    or isinstance(value, Decimal) and (value.is_signed() or value != value.to_integral_value())):
                rejected += 1
                continue
            value = int(value)
        accepted.append((claim, value))
    return accepted, rejected


def extract_source(source_id, actor) -> ExtractionResult:
    """One genuine provider call per request; accepted evidence remains unsigned."""
    from healthmesh.models import AIExtractionRun, InventoryClaim, SourceDocument, SourceSpan, StockBatch

    if (not actor or not actor.is_authenticated or not actor.is_active
            or not actor.groups.filter(name='coordinator').exists()):
        raise PermissionDenied('An active coordinator is required.')
    source = SourceDocument.objects.select_related('incident', 'facility').get(pk=source_id,
                                                                               incident__synthetic=True)
    if source.kind != 'RAW_TEXT' or source.facility_id is None:
        raise ValidationError({'source': 'Only facility-bound synthetic text can be extracted.'})
    batches = tuple(StockBatch.objects.filter(incident=source.incident, facility=source.facility)
                    .select_related('product'))
    if len(batches) != 1:
        raise ValidationError({'source': 'A single known stock batch is required for extraction.'})
    spans = tuple(SourceSpan.objects.filter(source=source).select_related('source').order_by('start', 'id'))
    if not spans:
        return ExtractionResult(status='INVALID_OUTPUT')
    provider = extract_claims(source.decoded_text, spans, 'v1')
    if provider.receipt is None:
        return provider
    if provider.status == 'SUCCESS':
        validated = validate_extraction(provider.raw_content, spans, batches[0].product.base_unit)
        accepted, rejected = _persistable_claims(validated)
        status = ('INVALID_OUTPUT' if len(validated.quarantined) == 1
                  and validated.quarantined[0].reason == 'INVALID_OUTPUT' else 'SUCCESS')
    else:
        accepted, rejected, status = [], 1, provider.status
    metadata = provider.receipt
    if status != metadata.status:
        metadata = metadata.model_copy(update={'status': status})
    with transaction.atomic():
        receipt = record_receipt(metadata)
        run = AIExtractionRun.objects.create(source=source, receipt=receipt,
                                              accepted_count=len(accepted), rejected_count=rejected)
        claim_rows = []
        if accepted:
            with incident_mutation(source.incident_id):
                for claim, value in accepted:
                    saved = InventoryClaim.objects.create(incident=source.incident,
                        subject_id=batches[0].id, source=source, span=next(
                            span for span in spans if span.id == claim.source_span_id),
                        extraction_run=run, field=claim.field, value=value,
                        observed_at=source.observed_at, status='OBSERVED_UNVERIFIED',
                        basis='AMD extracted observation; human verification required')
                    claim_rows.append({'id': str(saved.id), 'field': saved.field, 'value': saved.value,
                                       'status': saved.status, 'source_span_id': str(saved.span_id)})
                from healthmesh.evidence.conflicts import detect_conflicts
                detect_conflicts(source.incident_id)
    source.incident.refresh_from_db()
    return ExtractionResult(status=status, receipt=metadata, receipt_id=receipt.id,
                            claims=tuple(claim_rows), accepted_count=len(claim_rows),
                            rejected_count=rejected, new_revision=source.incident.revision)


def _grounded_result(context, kind, provider, fresh):
    """Persist only completed-call telemetry, then expose validated transient prose."""
    from .grounding import fallback_text, validate_grounded_content

    validated = (validate_grounded_content(provider.raw_content, context, kind)
                 if provider.status == 'SUCCESS' and fresh else None)
    status = ('SUCCESS' if validated is not None else
              'INVALID_OUTPUT' if provider.status == 'SUCCESS' else provider.status)
    metadata = provider.receipt
    if metadata is not None and metadata.status != status:
        metadata = metadata.model_copy(update={'status': status})
    receipt = record_receipt(metadata) if metadata is not None else None
    return {
        'status': status,
        'source': 'AMD_GENERATED_UNVERIFIED_WORDING' if validated else 'DETERMINISTIC_FALLBACK',
        'text': validated.text if validated else fallback_text(context, kind),
        'cited_span_ids': [str(value) for value in (
            validated.cited_span_ids if validated else tuple(line.span.id for line in context.lines))],
        'receipt_id': str(receipt.id) if receipt else None,
        'receipt': metadata.model_dump(mode='json') if metadata is not None else None,
    }


def _call_grounded(adapter, operation):
    from .types import GroundedExplanation

    if isinstance(adapter, UnavailableAMDAdapter):
        return operation(adapter)
    with admitted_call() as admitted:
        if not admitted:
            return GroundedExplanation(status='UNAVAILABLE')
        try:
            return operation(adapter)
        except TimeoutError:
            return GroundedExplanation(status='TIMEOUT')


def explain_conflict(conflict_id, actor):
    """One explicit coordinator call; conflict IDs and spans are loaded by the server."""
    from healthmesh.models import EvidenceConflict, Incident
    from .grounding import context_for_conflict

    if (not actor or not actor.is_authenticated or not actor.is_active
            or not actor.groups.filter(name='coordinator').exists()):
        raise PermissionDenied('An active coordinator is required.')
    conflict = EvidenceConflict.objects.select_related('incident').get(
        pk=conflict_id, incident__synthetic=True)
    context = context_for_conflict(conflict)
    revision = conflict.incident.revision
    claim_ids = tuple(conflict.claim_ids)
    adapter = get_amd_adapter()
    provider = _call_grounded(adapter,
                              lambda selected: selected.explain_conflict(conflict.id, context.spans))
    current = EvidenceConflict.objects.filter(pk=conflict.id).first()
    fresh = (Incident.objects.get(pk=context.incident_id).revision == revision
             and current is not None and current.state == 'OPEN'
             and tuple(current.claim_ids) == claim_ids)
    return _grounded_result(context, 'explanation', provider, fresh)


def draft_verification(task_id, actor):
    """An immutable selected task controls wording; no ranking or status changes."""
    from healthmesh.models import Incident, VerificationTask
    from .grounding import context_for_task

    if (not actor or not actor.is_authenticated or not actor.is_active
            or not actor.groups.filter(name='coordinator').exists()):
        raise PermissionDenied('An active coordinator is required.')
    task = VerificationTask.objects.select_related('incident').get(pk=task_id, incident__synthetic=True)
    context = context_for_task(task)
    revision = task.incident.revision
    adapter = get_amd_adapter()
    provider = _call_grounded(adapter,
                              lambda selected: selected.draft_verification(task, context.spans))
    fresh = (Incident.objects.get(pk=context.incident_id).revision == revision
             and VerificationTask.objects.filter(pk=task_id).exists())
    return _grounded_result(context, 'draft', provider, fresh)
