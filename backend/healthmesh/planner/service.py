"""No I/O, wall clock, model output, or mutation enters feasibility decisions."""
from datetime import timedelta
from itertools import combinations

from healthmesh.domain.types import IncidentSnapshot
from .types import Allocation, ConstraintCheck, PlanBranch, PlanResult


def _verified(snapshot, fact):
    # Canonical snapshot construction validates claim/value/source linkage. Here
    # require explicit evidence and a source belonging to this frozen incident;
    # status and timestamps alone cannot confer verification.
    return (fact.value is not None and fact.status in {'HUMAN_VERIFIED', 'DERIVED_FROM_VERIFIED'}
            and fact.evidence_id is not None and fact.source_id in snapshot.source_ids
            and fact.observed_at is not None and fact.observed_at.tzinfo is not None
            and timedelta(0) <= snapshot.as_of - fact.observed_at <= timedelta(hours=snapshot.policy.freshness_hours))


def _capacity(snapshot, batch):
    fields = ('on_hand', 'reserved', 'protected_reserve', 'reserve_accounting', 'expires_on', 'storage_usable')
    if not all(_verified(snapshot, getattr(batch, f)) for f in fields):
        return None
    if (batch.reserve_accounting.value != 'separate' or not batch.storage_usable.value
            or batch.expires_on.value < snapshot.as_of.date()
            or min(batch.on_hand.value, batch.reserved.value, batch.protected_reserve.value) < 0):
        return 0
    return max(0, batch.on_hand.value - batch.reserved.value - batch.protected_reserve.value)


def validate_proposal(snapshot: IncidentSnapshot, branch: PlanBranch) -> list[ConstraintCheck]:
    """Recompute every gate from input; never trust a branch's status or capacity."""
    checks = []

    def check(subject, field, passed, reason, evidence=()):
        checks.append(ConstraintCheck(subject_id=subject, field=field,
                                      status='UNKNOWN' if passed is None else 'PASS' if passed else 'FAIL',
                                      reason=reason, evidence_ids=evidence))

    def fact_check(subject, field, fact, predicate):
        known = _verified(snapshot, fact)
        check(subject, field, predicate(fact.value) if known else None,
              f'{subject}: {field} must have provenance, be verified, fresh and satisfy the constraint'
              if not known else f'{subject}: {field} constraint',
              (fact.evidence_id,) if fact.evidence_id else ())
        return known

    check(None, 'snapshot', branch.snapshot_id == snapshot.snapshot_id and branch.revision == snapshot.revision,
          'Proposal must refer to the exact frozen snapshot revision')
    request = snapshot.request
    if request is None:
        check(None, 'request', None, 'A verified recipient request is required')
        return checks
    quantity_known = fact_check(request.id, 'quantity', request.quantity, lambda v: v > 0)
    fact_check(request.id, 'is_net_transfer_request', request.is_net_transfer_request, lambda v: v is True)
    deadline_known = fact_check(request.id, 'need_by', request.need_by, lambda v: v >= snapshot.as_of)
    check(request.id, 'base_unit', bool(request.base_unit), 'Request must specify its base unit')
    check(request.id, 'full_fill', sum(a.quantity for a in branch.allocations) == request.quantity.value if quantity_known else None,
          'Allocations must fill the entire net transfer request')
    check(None, 'allocation_count', 1 <= len(branch.allocations) <= 2, 'A proposal must have one or two allocation legs')
    check(None, 'distinct_sources', len({a.facility_id for a in branch.allocations}) == len(branch.allocations),
          'Allocation legs must use distinct sending facilities')
    batches = {b.id: b for b in snapshot.batches}
    routes = {r.id: r for r in snapshot.routes}
    for allocation in branch.allocations:
        batch = batches.get(allocation.batch_id)
        check(allocation.batch_id, 'batch', batch is not None, 'Allocated batch must exist in snapshot')
        if batch is None:
            continue
        check(batch.id, 'identity', allocation.facility_id == batch.facility_id and allocation.product_id == batch.product_id
              and allocation.base_unit == batch.base_unit, 'Allocation identity must match its batch')
        check(batch.id, 'sender', batch.facility_id != request.facility_id, 'Recipient cannot supply itself')
        check(batch.id, 'product', batch.product_id == request.product_id, 'Product must match request')
        check(batch.id, 'unit', bool(batch.base_unit) and batch.base_unit == request.base_unit, 'Base unit must match request')
        check(batch.id, 'allocation_quantity', allocation.quantity > 0, 'Allocation quantity must be positive')
        known = []
        for field in ('on_hand', 'reserved', 'protected_reserve'):
            known.append(fact_check(batch.id, field, getattr(batch, field), lambda v: v >= 0))
        fact_check(batch.id, 'reserve_accounting', batch.reserve_accounting, lambda v: v == 'separate')
        fact_check(batch.id, 'expires_on', batch.expires_on, lambda v: v >= snapshot.as_of.date())
        fact_check(batch.id, 'storage_usable', batch.storage_usable, lambda v: v is True)
        check(batch.id, 'reserve_balance',
              allocation.quantity <= max(0, batch.on_hand.value - batch.reserved.value - batch.protected_reserve.value)
              if all(known) else None, 'Transfer must preserve reservations and protected reserve without a negative balance')
        route = routes.get(allocation.route_id)
        if route is None:
            check(batch.id, 'route_status', None, 'A verified open route to the recipient is required')
            continue
        check(route.id, 'route_endpoints', route.origin_id == batch.facility_id and route.destination_id == request.facility_id,
              'Route must connect the sender to the recipient')
        if route.status.value == 'UNKNOWN':
            check(route.id, 'route_status', None, 'Route must be verified OPEN',
                  (route.status.evidence_id,) if route.status.evidence_id else ())
        else:
            fact_check(route.id, 'route_status', route.status, lambda v: v == 'OPEN')
        eta_known = fact_check(route.id, 'verified_open_eta_hours', route.verified_open_eta_hours, lambda v: v > 0)
        if eta_known and route.verified_open_eta_hours.value > 0:
            arrival = snapshot.as_of + timedelta(hours=route.verified_open_eta_hours.value)
            check(route.id, 'deadline', arrival <= request.need_by.value if deadline_known else None,
                  'Delivery must arrive by the request deadline')
            check(batch.id, 'expiry_at_arrival', batch.expires_on.value >= arrival.date()
                  if _verified(snapshot, batch.expires_on) else None, 'Batch must remain unexpired through arrival')
    return checks


def plan(snapshot: IncidentSnapshot) -> PlanResult:
    """Enumerate singles and at most two greedy splits per pair of sources."""
    base = dict(snapshot_id=snapshot.snapshot_id, revision=snapshot.revision)
    request = snapshot.request
    if request is None or not _verified(snapshot, request.quantity) or request.quantity.value <= 0:
        probe = PlanBranch(**base, allocations=())
        checks = validate_proposal(snapshot, probe)
        return PlanResult(**base, status='BLOCKED_INSUFFICIENT_EVIDENCE',
                          mandatory_unverified_facts=tuple(c for c in checks if c.status == 'UNKNOWN'),
                          infeasible_reasons=tuple(c.reason for c in checks if c.status == 'FAIL'))
    target = request.quantity.value
    candidates = []
    for batch in sorted(snapshot.batches, key=lambda b: (b.facility_name, str(b.id))):
        if batch.facility_id == request.facility_id:
            continue
        route = next(iter(sorted((r for r in snapshot.routes if r.origin_id == batch.facility_id
                                  and r.destination_id == request.facility_id), key=lambda r: str(r.id))), None)
        capacity = _capacity(snapshot, batch)
        candidates.append(Allocation(batch_id=batch.id, facility_id=batch.facility_id, facility_name=batch.facility_name,
                                     route_id=route.id if route else None, product_id=batch.product_id, base_unit=batch.base_unit,
                                     quantity=min(target, capacity) if capacity is not None else target, transferable=capacity))

    def evaluate(allocations):
        branch = PlanBranch(**base, allocations=tuple(allocations))
        checks = tuple(validate_proposal(snapshot, branch))
        failures = tuple(c.reason for c in checks if c.status == 'FAIL')
        conditions = tuple(c.reason for c in checks if c.status == 'UNKNOWN')
        return branch.model_copy(update=dict(checks=checks, rejected_reasons=failures, conditions=conditions,
                                             status='INFEASIBLE' if failures else 'PROVISIONAL' if conditions else 'READY_FOR_REVIEW'))

    branches = [evaluate((candidate,)) for candidate in candidates]
    has_provisional_single = any(b.status == 'PROVISIONAL' for b in branches)
    if not any(b.status == 'READY_FOR_REVIEW' for b in branches):
        seen = set()
        for left, right in combinations(candidates, 2):
            if left.facility_id == right.facility_id:
                continue
            for first, second in ((left, right), (right, left)):
                first_quantity = min(target - 1, first.quantity)
                second_quantity = min(target - first_quantity, second.quantity)
                if min(first_quantity, second_quantity) <= 0:
                    continue
                legs = tuple(sorted((first.model_copy(update={'quantity': first_quantity}),
                                     second.model_copy(update={'quantity': second_quantity})), key=lambda a: str(a.batch_id)))
                key = tuple((a.batch_id, a.quantity) for a in legs)
                if key not in seen:
                    seen.add(key)
                    combined = evaluate(legs)
                    # Existing full-fill conditional singles already express the
                    # pending path. Still surface any fully verified split.
                    if combined.status == 'READY_FOR_REVIEW' or not has_provisional_single:
                        branches.append(combined)

    def ranking(branch):
        routes = {r.id: r for r in snapshot.routes}
        travel = sum(routes[a.route_id].verified_open_eta_hours.value or 0 for a in branch.allocations if a.route_id in routes)
        return ({'READY_FOR_REVIEW': 0, 'PROVISIONAL': 1, 'INFEASIBLE': 2}[branch.status],
                -sum(a.quantity for a in branch.allocations), travel,
                tuple((a.facility_name, str(a.batch_id), a.quantity) for a in branch.allocations))

    branches.sort(key=ranking)
    checks = [c for b in branches for c in b.checks]
    unknown = tuple(dict.fromkeys(c for c in checks if c.status == 'UNKNOWN'))
    failures = tuple(dict.fromkeys(c.reason for c in checks if c.status == 'FAIL'))
    status = ('READY_FOR_REVIEW' if any(b.status == 'READY_FOR_REVIEW' for b in branches) else
              'PROVISIONAL' if any(b.status == 'PROVISIONAL' for b in branches) else
              'NO_FEASIBLE_OPTION')
    return PlanResult(**base, status=status, branches=tuple(branches), mandatory_unverified_facts=unknown,
                      evidence_ids=tuple(sorted({e for c in checks for e in c.evidence_ids}, key=str)),
                      infeasible_reasons=failures or (() if branches else ('No candidate sending facilities',)))
