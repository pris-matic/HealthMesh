"""Django model discovery entry point."""
from .db_models.core import CanonicalFact, Facility, Incident, Product, RouteObservation, StockBatch, SupplyRequest
from .db_models.sources import InventoryClaim, SourceDocument, SourceSpan
from .db_models.planning import PlanRun, PlanProposal
from .db_models.ai import AIExtractionRun, AIInferenceReceipt, AMDCallGate

__all__ = ['AIExtractionRun', 'AIInferenceReceipt', 'AMDCallGate', 'CanonicalFact', 'Facility', 'Incident', 'Product', 'RouteObservation',
           'StockBatch', 'SupplyRequest', 'InventoryClaim', 'SourceDocument', 'SourceSpan', 'PlanRun', 'PlanProposal']
