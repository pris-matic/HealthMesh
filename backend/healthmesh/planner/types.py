from typing import Literal
from uuid import UUID

from healthmesh.domain.types import FrozenModel


class ConstraintCheck(FrozenModel):
    subject_id: UUID | None = None
    field: str
    status: Literal['PASS', 'FAIL', 'UNKNOWN']
    reason: str
    evidence_ids: tuple[UUID, ...] = ()


class Allocation(FrozenModel):
    batch_id: UUID
    facility_id: UUID
    facility_name: str
    route_id: UUID | None
    product_id: UUID
    base_unit: str
    quantity: int
    transferable: int | None


class PlanBranch(FrozenModel):
    snapshot_id: str
    revision: int
    allocations: tuple[Allocation, ...]
    status: Literal['PROVISIONAL', 'READY_FOR_REVIEW', 'INFEASIBLE'] = 'PROVISIONAL'
    conditions: tuple[str, ...] = ()
    checks: tuple[ConstraintCheck, ...] = ()
    rejected_reasons: tuple[str, ...] = ()


class PlanResult(FrozenModel):
    status: Literal['PROVISIONAL', 'READY_FOR_REVIEW', 'BLOCKED_INSUFFICIENT_EVIDENCE', 'NO_FEASIBLE_OPTION']
    snapshot_id: str
    revision: int
    branches: tuple[PlanBranch, ...] = ()
    mandatory_unverified_facts: tuple[ConstraintCheck, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    infeasible_reasons: tuple[str, ...] = ()
    requires_review: Literal[True] = True
    model_policy_version: str = 'DETERMINISTIC_PLANNER_V1'
