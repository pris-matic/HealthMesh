"""Provider-independent adapter contract and explicit unavailable state."""
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from django.conf import settings

from .types import ExtractionResult, GroundedExplanation, SuggestedMessage

if TYPE_CHECKING:
    from healthmesh.models import SourceSpan


class VerificationTask(Protocol):
    id: UUID


class AMDInferenceAdapter(ABC):
    # Hard overall deadline, bounded request size and issued evidence scope.
    timeout_seconds = 10
    max_retries = 2
    max_input_bytes = 65536
    max_spans = 128

    @abstractmethod
    def health(self) -> dict[str, str]: ...

    @abstractmethod
    def extract_claims(self, text: str, allowed_spans: Sequence['SourceSpan'], schema_version: str) -> ExtractionResult: ...

    @abstractmethod
    def explain_conflict(self, conflict_id: UUID, evidence: Sequence['SourceSpan']) -> GroundedExplanation: ...

    @abstractmethod
    def draft_verification(self, task: VerificationTask, evidence: Sequence['SourceSpan']) -> SuggestedMessage: ...


class UnavailableAMDAdapter(AMDInferenceAdapter):
    def health(self) -> dict[str, str]:
        configured = all(isinstance(value, str) and value.strip() for value in (
            settings.AMD_API_KEY, settings.AMD_API_BASE_URL, settings.AMD_MODEL_ID))
        return {'status': 'UNAVAILABLE', 'reason': 'provider_transport_unverified' if configured else 'missing_configuration'}

    def extract_claims(self, text, allowed_spans, schema_version) -> ExtractionResult:
        if (not isinstance(text, str) or len(text.encode('utf-8')) > self.max_input_bytes
                or len(allowed_spans) > self.max_spans or schema_version != 'v1'):
            return ExtractionResult(status='INVALID_OUTPUT')
        return ExtractionResult(status='UNAVAILABLE')

    def explain_conflict(self, conflict_id, evidence) -> GroundedExplanation:
        return GroundedExplanation(status='INVALID_OUTPUT' if len(evidence) > self.max_spans else 'UNAVAILABLE')

    def draft_verification(self, task, evidence) -> SuggestedMessage:
        return SuggestedMessage(status='INVALID_OUTPUT' if len(evidence) > self.max_spans else 'UNAVAILABLE')
