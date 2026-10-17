"""Provider-independent outcomes and an allowlist of completed-call metadata."""
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # Raw candidates require Task 7 validation before storage as unverified claims.
    claims: tuple[dict[str, object], ...] = ()

    @model_validator(mode='after')
    def no_failed_claims(self):
        if self.status != 'SUCCESS' and self.claims:
            raise ValueError('Failed inference cannot produce candidate claims.')
        return self


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
