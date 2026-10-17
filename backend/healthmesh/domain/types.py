"""Frozen planning input. Evidence values never confer verification by themselves."""
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from healthmesh.scenarios.fixture import Policy


class FactStatus(StrEnum):
    OBSERVED_UNVERIFIED = 'OBSERVED_UNVERIFIED'
    CONFLICTED = 'CONFLICTED'
    HUMAN_VERIFIED = 'HUMAN_VERIFIED'
    DERIVED_FROM_VERIFIED = 'DERIVED_FROM_VERIFIED'
    UNKNOWN = 'UNKNOWN'
    STALE = 'STALE'


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError('Snapshot mappings are immutable.')

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


def freeze(value):
    if isinstance(value, dict):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class SnapshotPolicy(Policy):
    @field_validator('unit_aliases')
    @classmethod
    def immutable_aliases(cls, value):
        return FrozenDict(value)


T = TypeVar('T')


class PlanningFact(FrozenModel, Generic[T]):
    value: T | None = None
    status: FactStatus = FactStatus.UNKNOWN
    observed_at: datetime | None = None
    evidence_id: UUID | None = None
    source_id: UUID | None = None
    basis: str = 'No supporting canonical evidence'


class DemandPoint(FrozenModel):
    date: date
    units: int


class FacilitySnapshot(FrozenModel):
    id: UUID
    name: str
    daily_demand: PlanningFact[tuple[DemandPoint, ...]]


class BatchSnapshot(FrozenModel):
    id: UUID
    facility_id: UUID
    facility_name: str
    product_id: UUID
    product_code: str
    base_unit: str
    batch_code: str
    on_hand: PlanningFact[int]
    reserved: PlanningFact[int]
    protected_reserve: PlanningFact[int]
    reserve_accounting: PlanningFact[str]
    expires_on: PlanningFact[date]
    storage_usable: PlanningFact[bool]


class RequestSnapshot(FrozenModel):
    id: UUID
    facility_id: UUID
    product_id: UUID
    base_unit: str
    quantity: PlanningFact[int]
    is_net_transfer_request: PlanningFact[bool]
    need_by: PlanningFact[datetime]


class RouteSnapshot(FrozenModel):
    id: UUID
    origin_id: UUID
    origin_name: str
    destination_id: UUID
    status: PlanningFact[str]
    verified_open_eta_hours: PlanningFact[int]


class ClaimSnapshot(FrozenModel):
    id: UUID
    subject_id: UUID
    field: str
    value: Any
    status: FactStatus
    observed_at: datetime
    source_id: UUID
    span_id: UUID | None
    basis: str

    @field_validator('value', mode='after')
    @classmethod
    def immutable_value(cls, value):
        return freeze(value)


class IncidentSnapshot(FrozenModel):
    incident_id: UUID
    revision: int
    as_of: datetime
    request: RequestSnapshot | None
    batches: tuple[BatchSnapshot, ...]
    facilities: tuple[FacilitySnapshot, ...]
    routes: tuple[RouteSnapshot, ...]
    claims: tuple[ClaimSnapshot, ...]
    source_ids: tuple[UUID, ...]
    policy: SnapshotPolicy

    @computed_field
    @property
    def snapshot_id(self) -> str:
        return f'{self.incident_id}:{self.revision}'
