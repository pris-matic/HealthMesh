"""Provider-independent outcomes and an allowlist of completed-call metadata."""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

InferenceStatus = Literal['SUCCESS', 'UNAVAILABLE', 'INVALID_OUTPUT', 'TIMEOUT']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ReceiptMetadata(Contract):
    provider: Literal['AMD'] = 'AMD'
    model_id: str = Field(min_length=1, max_length=200)
    timestamp: datetime
    latency_ms: int = Field(ge=0, strict=True)
    status: Literal['SUCCESS', 'INVALID_OUTPUT']
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    input_schema_version: str = Field(min_length=1, max_length=40)
    output_schema_version: str = Field(min_length=1, max_length=40)

    @field_validator('timestamp')
    @classmethod
    def require_utc(cls, value):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError('A real timezone-aware UTC call timestamp is required.')
        return value


class Outcome(Contract):
    status: InferenceStatus
    receipt: ReceiptMetadata | None = None

    @model_validator(mode='after')
    def completed_receipt(self):
        if self.status == 'SUCCESS' and self.receipt is None:
            raise ValueError('Success requires a completed-call receipt.')
        if self.receipt is not None and self.receipt.status != self.status:
            raise ValueError('Receipt status must match the completed outcome.')
        return self


class ExtractionResult(Outcome):
    # This transport-only value is never serialized, logged or persisted. The
    # orchestration service hands it unchanged to the exact-decimal validator.
    raw_content: str | None = Field(default=None, exclude=True, repr=False)
    claims: tuple[dict[str, object], ...] = ()
    receipt_id: UUID | None = None
    accepted_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    new_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def no_failed_claims(self):
        if self.status != 'SUCCESS' and self.claims:
            raise ValueError('Failed inference cannot produce candidate claims.')
        return self


class ExtractedClaim(Contract):
    """v1 candidate: model confidence is metadata, never verification."""
    field: StrictStr
    # Decoded floats cannot prove their original JSON precision. Raw JSON is
    # parsed with Decimal by validate_extraction; no amount passes via float.
    value: StrictInt | Annotated[Decimal, Field(strict=True, allow_inf_nan=False)] | StrictStr
    unit: StrictStr | None = None
    source_span_id: UUID
    quote: StrictStr | None = None
    is_ambiguous: bool = Field(default=False, strict=True)
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)

    @model_validator(mode='after')
    def valid_value(self):
        if isinstance(self.value, (int, Decimal)):
            if (isinstance(self.value, bool) or self.value < 0
                    or isinstance(self.value, Decimal) and self.value.is_signed()):
                raise ValueError('Amounts must be finite and nonnegative.')
        if self.field in {'on_hand', 'reserved'} and type(self.value) not in {int, Decimal}:
            raise ValueError('Quantities must be JSON numbers.')
        if self.field in {'observed_at', 'uncertainty'} and (not isinstance(self.value, str) or not self.value.strip()):
            raise ValueError('Dates and uncertainty require nonempty text.')
        return self


class ValidatedClaim(ExtractedClaim):
    quote: str
    status: Literal['OBSERVED_UNVERIFIED'] = 'OBSERVED_UNVERIFIED'


class QuarantinedClaim(Contract):
    # Do not retain arbitrary model output or instructions in the result.
    index: int | None = None
    reason: Literal['INVALID_OUTPUT', 'INVALID_CLAIM', 'UNSUPPORTED_FIELD',
                    'UNKNOWN_SPAN', 'INVALID_SPAN', 'QUOTE_MISMATCH',
                    'UNIT_MISMATCH', 'VALUE_NOT_SUPPORTED']


class ValidatedClaims(Contract):
    accepted: tuple[ValidatedClaim, ...] = ()
    quarantined: tuple[QuarantinedClaim, ...] = ()


class GroundedExplanation(Outcome):
    text: str | None = Field(default=None, max_length=4000)
    cited_span_ids: tuple[UUID, ...] = ()

    @model_validator(mode='after')
    def no_failed_wording(self):
        if self.status != 'SUCCESS' and (self.text is not None or self.cited_span_ids):
            raise ValueError('Failed inference cannot produce wording or citations.')
        return self


class SuggestedMessage(GroundedExplanation):
    pass
