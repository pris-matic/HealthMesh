"""Pure conditional planning over frozen evidence snapshots."""

from .service import plan, validate_proposal
from .types import Allocation, ConstraintCheck, PlanBranch, PlanResult

__all__ = ['plan', 'validate_proposal', 'Allocation', 'ConstraintCheck', 'PlanBranch', 'PlanResult']
