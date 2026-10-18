"""Public, immutable verification ranking contracts."""
from typing import Literal
from uuid import UUID

from healthmesh.domain.types import FrozenModel


class PossibleEffect(FrozenModel):
    answer: str
    target_branch_status: str
    branch_statuses: tuple[str, ...]


class RankedVerification(FrozenModel):
    subject_id: UUID
    facility_name: str
    field: Literal['on_hand', 'route_status']
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[UUID, ...]
    staff_minutes: int
    response_delay_minutes: int
    sensitivity_count: int
    sensitivity_per_staff_minute: float
    net_value_estimate: float | None
    possible_effects: tuple[PossibleEffect, ...]


class RankingResult(FrozenModel):
    snapshot_id: str
    revision: int
    tasks: tuple[RankedVerification, ...]
    valuation_mode: Literal['DETERMINISTIC_SENSITIVITY', 'WEIGHTED_EXPECTED_LOSS']
    net_value_estimate: float | None
    must_verify_before_approval: bool
    remaining_staff_minutes: int
    remaining_questions: int
