"""Django model discovery entry point."""
from .db_models.core import CanonicalFact, Facility, Incident, Product, RouteObservation, StockBatch, SupplyRequest
from .db_models.sources import InventoryClaim, SourceDocument, SourceSpan
from .db_models.planning import PlanRun, PlanProposal
from .db_models.ai import AIExtractionRun, AIInferenceReceipt, AMDCallGate
from .db_models.evidence import EvidenceConflict

__all__ = ['AIExtractionRun', 'AIInferenceReceipt', 'AMDCallGate', 'EvidenceConflict', 'CanonicalFact', 'Facility', 'Incident', 'Product', 'RouteObservation',
           'StockBatch', 'SupplyRequest', 'InventoryClaim', 'SourceDocument', 'SourceSpan', 'PlanRun', 'PlanProposal']
