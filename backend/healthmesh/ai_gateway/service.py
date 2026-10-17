"""No incident mutation or raw-upload access belongs in inference orchestration."""
from collections.abc import Sequence

from django.core.exceptions import ValidationError

from healthmesh.sources.sanitizer import sanitize
from healthmesh.sources.spans import valid_span_offsets
from .adapter import AMDInferenceAdapter, UnavailableAMDAdapter
from .types import ExtractionResult, ReceiptMetadata


def get_amd_adapter() -> AMDInferenceAdapter:
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
    try:
        return get_amd_adapter().extract_claims(sanitized, allowed_spans, schema_version)
    except TimeoutError:
        return ExtractionResult(status='TIMEOUT')


def record_receipt(metadata: ReceiptMetadata):
    """Persist completed-call metadata without manufacturing timestamps or IDs.

    Only verified transport may supply genuine metadata. No prompt, headers,
    raw response or exception can be passed through this allowlisted contract.
    """
    from healthmesh.models import AIInferenceReceipt
    return AIInferenceReceipt.objects.create(**metadata.model_dump())
