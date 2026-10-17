"""Validated public scenario seeds. No evaluation truth enters this module."""

from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[3]
SCENARIO_DIR = ROOT / "fixtures" / "scenarios"
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ScenarioValidationError(ValueError):
    """A seed is unavailable or violates the public scenario contract."""


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def require_utc(cls, value):
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value


class Product(PublicModel):
    id: UUID
    code: Literal["SIM-MX-01"]
    base_unit: Literal["box"]


class Stock(PublicModel):
    batch_id: UUID
    batch_code: str
    on_hand: NonnegativeInt
    reserved: NonnegativeInt
    protected_reserve: NonnegativeInt
    reserve_accounting: Literal["separate"]
    expires_on: date
    storage_usable: bool
    observed_at: datetime
    count_observed_at: datetime
    count_source_id: UUID
    current_facts_source_id: UUID


class Demand(PublicModel):
    date: date
    units: NonnegativeInt


class Facility(PublicModel):
    id: UUID
    name: Literal["Aster", "Brook", "Cedar"]
    stock: Stock
    daily_demand: tuple[Demand, ...] = Field(min_length=14, max_length=14)


class Request(PublicModel):
    id: UUID
    facility_id: UUID
    quantity: Annotated[int, Field(strict=True, gt=0)]
    is_net_transfer_request: Literal[True]
    need_by: datetime
    source_id: UUID


class Route(PublicModel):
    id: UUID
    origin_id: UUID
    destination_id: UUID
    status: Literal["OPEN", "CLOSED", "UNKNOWN"]
    verified_open_eta_hours: Annotated[int, Field(strict=True, gt=0)]
    observed_at: datetime
    source_id: UUID


class PublicFacts(PublicModel):
    as_of: datetime
    product: Product
    facilities: tuple[Facility, ...] = Field(min_length=3, max_length=3)
    request: Request
    routes: tuple[Route, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def check_relations(self):
        by_name = {facility.name: facility for facility in self.facilities}
        ids = {facility.id for facility in self.facilities}
        if set(by_name) != {"Aster", "Brook", "Cedar"} or len(ids) != 3:
            raise ValueError("three distinct synthetic facilities required")
        aster = by_name["Aster"]
        if self.request.facility_id != aster.id or self.request.need_by <= self.as_of:
            raise ValueError("request must target Aster after as_of")
        if {route.origin_id for route in self.routes} != ids - {aster.id}:
            raise ValueError("one route from each donor is required")
        for route in self.routes:
            if route.destination_id != aster.id or route.observed_at > self.as_of:
                raise ValueError("invalid route destination or observation time")
        expected_days = tuple(self.as_of.date() - timedelta(days=n) for n in range(14, 0, -1))
        for facility in self.facilities:
            if tuple(row.date for row in facility.daily_demand) != expected_days:
                raise ValueError("demand must cover the preceding 14 days in order")
            if max(facility.stock.observed_at, facility.stock.count_observed_at) > self.as_of:
                raise ValueError("stock observations cannot be in the future")
        return self


class SourceReference(PublicModel):
    id: UUID
    kind: Literal["SIGNED_FIXTURE", "RAW_TEXT"]
    observed_at: datetime
    verified: bool
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    text: str | None = None

    @model_validator(mode="after")
    def check_source(self):
        if self.kind == "RAW_TEXT":
            if self.verified or not self.path or not self.sha256:
                raise ValueError("raw text requires a path/hash and must stay unverified")
        elif not self.verified or any((self.path, self.sha256, self.text)):
            raise ValueError("signed fixture facts must be verified and inline")
        return self


class VerificationBudget(PublicModel):
    staff_minutes: NonnegativeInt
    questions: NonnegativeInt


class VerificationTask(PublicModel):
    facility_id: UUID
    field: Literal["on_hand", "route_status"]
    staff_minutes: Annotated[int, Field(strict=True, gt=0)]
    response_delay_minutes: NonnegativeInt


class PublicPossibilities(PublicModel):
    synthetic: Literal[True]
    brook_on_hand: tuple[NonnegativeInt, ...]
    cedar_route: tuple[Literal["CLOSED", "OPEN"], ...]
    weights: None


class LossPolicy(PublicModel):
    version: Literal["LOSS_POLICY_V1"]
    unmet_units_by_deadline: NonnegativeInt
    travel_hours: NonnegativeInt
    verification_staff_minutes: Annotated[float, Field(ge=0)]
    response_delay_hours: NonnegativeInt


class Policy(PublicModel):
    freshness_hours: Annotated[int, Field(strict=True, gt=0)]
    coverage_alert_days: Annotated[int, Field(strict=True, gt=0)]
    protected_reserve_counts_for_local_coverage: Literal[True]
    unit_aliases: dict[Literal["boxes"], Literal["box"]]
    verification_budget: VerificationBudget
    verification_tasks: tuple[VerificationTask, ...]
    public_possibilities: PublicPossibilities
    loss: LossPolicy


class ScenarioSeed(PublicModel):
    scenario_id: Literal["storm-supply-01"]
    fixture_revision: Annotated[int, Field(strict=True, gt=0)]
    synthetic: Literal[True]
    public_facts: PublicFacts
    source_references: tuple[SourceReference, ...]
    policy: Policy

    @model_validator(mode="after")
    def check_provenance(self):
        sources = {source.id: source for source in self.source_references}
        if len(sources) != len(self.source_references):
            raise ValueError("source identities must be unique")
        facts = self.public_facts
        required = [(facts.request.source_id, None)]
        for facility in facts.facilities:
            stock = facility.stock
            required.extend(((stock.count_source_id, stock.count_observed_at),
                             (stock.current_facts_source_id, stock.observed_at)))
        required.extend((route.source_id, route.observed_at) for route in facts.routes)
        for source_id, observed_at in required:
            source = sources.get(source_id)
            if source is None or not source.verified:
                raise ValueError("canonical fixture facts require a signed source")
            if observed_at is not None and source.observed_at != observed_at:
                raise ValueError("fact and source observation times must match")
        if any(source.observed_at > facts.as_of for source in sources.values()):
            raise ValueError("source observations cannot be in the future")
        ids = {facility.id for facility in facts.facilities}
        if any(task.facility_id not in ids for task in self.policy.verification_tasks):
            raise ValueError("verification task references an unknown facility")
        return self


def load_scenario(scenario_id: str) -> ScenarioSeed:
    """Load only a public seed, preserving raw text without extracting claims."""
    if not isinstance(scenario_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scenario_id):
        raise ScenarioValidationError("invalid public scenario ID")
    try:
        seed = ScenarioSeed.model_validate_json((SCENARIO_DIR / f"{scenario_id}.json").read_bytes())
        if seed.scenario_id != scenario_id:
            raise ScenarioValidationError("scenario ID does not match its filename")
        hydrated = []
        for source in seed.source_references:
            if source.kind == "RAW_TEXT":
                path = (ROOT / source.path).resolve()
                if not path.is_relative_to((ROOT / "fixtures" / "sources").resolve()):
                    raise ScenarioValidationError("raw source must be under fixtures/sources")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != source.sha256:
                    raise ScenarioValidationError("raw source SHA-256 mismatch")
                if source.text is not None:
                    raise ScenarioValidationError("raw source text must come from the hashed source")
                source = source.model_copy(update={"text": content.decode("utf-8")})
            hydrated.append(source)
        return seed.model_copy(update={"source_references": tuple(hydrated)})
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ScenarioValidationError(str(exc)) from exc
