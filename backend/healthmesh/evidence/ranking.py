"""Rank public synthetic questions without touching database or hidden truth."""
from datetime import timedelta
from itertools import combinations
from uuid import UUID

from healthmesh.domain.types import IncidentSnapshot, PlanningFact
from healthmesh.planner.service import _verified, plan, validate_proposal
from healthmesh.planner.types import PlanResult
from healthmesh.scenarios.fixture import PublicPossibilities, VerificationBudget
from .types import PossibleEffect, RankedVerification, RankingResult


_PRIVATE_SOURCE = UUID(int=1)
_PRIVATE_CLAIM = UUID(int=2)


def _candidate(snapshot, policy_task):
    facility = next((row for row in snapshot.facilities if row.id == policy_task.facility_id), None)
    if facility is None:
        return None
    if policy_task.field == 'on_hand':
        subject = next((row for row in snapshot.batches if row.facility_id == facility.id), None)
    else:
        subject = next((row for row in snapshot.routes if row.origin_id == facility.id and
                        snapshot.request and row.destination_id == snapshot.request.facility_id), None)
    return (facility.name, subject) if subject else None


def _hypothetical(snapshot, answers, delay_minutes):
    """Private provenance exists only in this cloned planner input."""
    as_of = snapshot.as_of + timedelta(minutes=delay_minutes)

    def fact(value):
        return PlanningFact(value=value, status='HUMAN_VERIFIED', observed_at=as_of,
                            evidence_id=_PRIVATE_CLAIM, source_id=_PRIVATE_SOURCE,
                            basis='Private in-memory public-world hypothesis')

    batches = tuple(row.model_copy(update={'on_hand': fact(answers['on_hand'])})
                    if row.facility_name == 'Brook' and 'on_hand' in answers else row for row in snapshot.batches)
    routes = tuple(row.model_copy(update={'status': fact(answers['route_status'])})
                   if row.origin_name == 'Cedar' and 'route_status' in answers else row for row in snapshot.routes)
    return snapshot.model_copy(update={'as_of': as_of, 'batches': batches, 'routes': routes,
                                       'source_ids': snapshot.source_ids + (_PRIVATE_SOURCE,)})


def _target_status(result, facility_name):
    return next((branch.status for branch in result.branches
                 if len(branch.allocations) == 1 and branch.allocations[0].facility_name == facility_name),
                'INFEASIBLE')


def _preferred_guarded_identity(snapshot, result):
    branch = next((row for row in result.branches if row.status == 'READY_FOR_REVIEW' and
                   all(check.status == 'PASS' for check in validate_proposal(snapshot, row))), None)
    if branch is None:
        return None
    return tuple((allocation.facility_name, allocation.quantity) for allocation in branch.allocations)


def _loss(snapshot, result, staff_minutes, delay_minutes):
    request = snapshot.request
    if request is None or request.quantity.value is None:
        return float('inf')
    ready = [branch for branch in result.branches if branch.status == 'READY_FOR_REVIEW'
             and all(check.status == 'PASS' for check in validate_proposal(snapshot, branch))]
    travel = min((sum(next(route.verified_open_eta_hours.value for route in snapshot.routes
                           if route.id == allocation.route_id) for allocation in branch.allocations)
                  for branch in ready), default=0)
    policy = snapshot.policy.loss
    return (0 if ready else request.quantity.value * policy.unmet_units_by_deadline) + (
        travel * policy.travel_hours + staff_minutes * policy.verification_staff_minutes +
        delay_minutes / 60 * policy.response_delay_hours)


def _known_public_answer(snapshot, field):
    policy_task = next((task for task in snapshot.policy.verification_tasks if task.field == field), None)
    selected = _candidate(snapshot, policy_task) if policy_task else None
    if selected is None:
        return None
    _, subject = selected
    fact = subject.on_hand if field == 'on_hand' else subject.status
    return fact.value if _verified(snapshot, fact) else None


def _conditioned_worlds(snapshot, possibilities):
    if possibilities.weights is None:
        return None
    brook = _known_public_answer(snapshot, 'on_hand')
    cedar = _known_public_answer(snapshot, 'route_status')
    remaining = tuple(world for world in possibilities.weights
                      if (brook is None or world.brook_on_hand == brook)
                      and (cedar is None or world.cedar_route == cedar))
    total = sum(world.weight for world in remaining)
    # A verified value outside the public support invalidates this prior.
    return tuple((world, world.weight / total) for world in remaining) if total else None


def _weighted_value(snapshot, baseline, field, delay_minutes, staff_minutes, worlds):
    base_loss = _loss(snapshot, baseline, 0, 0)
    weighted_loss = 0.0
    for world, probability in worlds:
        answer = world.brook_on_hand if field == 'on_hand' else world.cedar_route
        hypothetical = _hypothetical(snapshot, {field: answer}, delay_minutes)
        weighted_loss += probability * _loss(hypothetical, plan(hypothetical), staff_minutes, delay_minutes)
    return base_loss - weighted_loss


def rank_verifications(snapshot: IncidentSnapshot, plan_result: PlanResult,
                       budget: VerificationBudget) -> RankingResult:
    if plan_result.snapshot_id != snapshot.snapshot_id or plan_result.revision != snapshot.revision:
        raise ValueError('Plan and snapshot revisions must match.')
    possibilities = PublicPossibilities.model_validate(snapshot.policy.public_possibilities.model_dump())
    worlds = _conditioned_worlds(snapshot, possibilities)
    weighted = worlds is not None
    mode = 'WEIGHTED_EXPECTED_LOSS' if weighted else 'DETERMINISTIC_SENSITIVITY'
    tasks = []
    request = snapshot.request
    for policy_task in snapshot.policy.verification_tasks:
        selected = _candidate(snapshot, policy_task)
        if (selected is None or request is None or not _verified(snapshot, request.need_by)
                or budget.questions < 1 or policy_task.staff_minutes > budget.staff_minutes):
            continue
        facility_name, subject = selected
        if snapshot.as_of + timedelta(minutes=policy_task.response_delay_minutes) >= request.need_by.value:
            continue
        answers = (possibilities.brook_on_hand if policy_task.field == 'on_hand' else possibilities.cedar_route)
        if len(answers) < 2:
            continue
        current_fact = subject.on_hand if policy_task.field == 'on_hand' else subject.status
        # A current signed-off fact needs no optional question; a conflict is not signed off.
        if _verified(snapshot, current_fact):
            continue
        effects = []
        answer_results = []
        for answer in answers:
            simulated = _hypothetical(snapshot, {policy_task.field: answer}, policy_task.response_delay_minutes)
            result = plan(simulated)
            answer_results.append((simulated, result))
            effects.append(PossibleEffect(answer=str(answer), target_branch_status=_target_status(result, facility_name),
                                          branch_statuses=tuple(branch.status for branch in result.branches)))
        preferred = _preferred_guarded_identity(snapshot, plan_result)
        if all(_preferred_guarded_identity(world, result) == preferred
               for world, result in answer_results):
            continue
        other_field = 'route_status' if policy_task.field == 'on_hand' else 'on_hand'
        known_other = _known_public_answer(snapshot, other_field)
        other_answers = ((known_other,) if known_other is not None else
                         possibilities.cedar_route if other_field == 'route_status' else possibilities.brook_on_hand)
        sensitivity = 0
        for other in other_answers:
            states = []
            for answer in answers:
                world = _hypothetical(snapshot, {policy_task.field: answer, other_field: other},
                                      policy_task.response_delay_minutes)
                states.append(_preferred_guarded_identity(world, plan(world)))
            sensitivity += sum(left != right for left, right in combinations(states, 2))
        if not sensitivity:
            continue
        value = (_weighted_value(snapshot, plan_result, policy_task.field,
                                 policy_task.response_delay_minutes, policy_task.staff_minutes, worlds)
                 if weighted else None)
        if weighted and value <= 0:
            continue
        evidence_ids = tuple(sorted({claim.id for claim in snapshot.claims if claim.subject_id == subject.id
                                     and claim.field == policy_task.field}, key=str))
        tasks.append(RankedVerification(
            subject_id=subject.id, facility_name=facility_name, field=policy_task.field,
            reason_codes=('DECISION_SENSITIVE',), evidence_ids=evidence_ids,
            staff_minutes=policy_task.staff_minutes, response_delay_minutes=policy_task.response_delay_minutes,
            sensitivity_count=sensitivity, sensitivity_per_staff_minute=sensitivity / policy_task.staff_minutes,
            net_value_estimate=value, possible_effects=tuple(effects)))
    tasks.sort(key=lambda row: (-(row.net_value_estimate if weighted else row.sensitivity_per_staff_minute),
                                row.staff_minutes, row.facility_name, row.field, str(row.subject_id)))
    return RankingResult(snapshot_id=snapshot.snapshot_id, revision=snapshot.revision,
                         tasks=tuple(tasks[:3]), valuation_mode=mode,
                         net_value_estimate=tasks[0].net_value_estimate if tasks else None,
                         must_verify_before_approval=bool(plan_result.mandatory_unverified_facts)
                         and not any(branch.status == 'READY_FOR_REVIEW' for branch in plan_result.branches),
                         remaining_staff_minutes=budget.staff_minutes, remaining_questions=budget.questions)
