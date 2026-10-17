"""Project canonical state under the same incident lock used for all writes."""
from datetime import timedelta
import json
from uuid import UUID

from django.db import transaction
from django.core.exceptions import ValidationError

from healthmesh.models import CanonicalFact, Facility, Incident, InventoryClaim, RouteObservation, SourceDocument, StockBatch, SupplyRequest
from healthmesh.db_models.core import mutation_in_progress
from .types import BatchSnapshot, ClaimSnapshot, FacilitySnapshot, IncidentSnapshot, RequestSnapshot, RouteSnapshot, SnapshotPolicy


@transaction.atomic
def build_snapshot(incident_id: UUID) -> IncidentSnapshot:
    incident = Incident.objects.select_for_update().get(pk=incident_id)
    if mutation_in_progress(incident.id):
        raise ValidationError('Build a snapshot after the logical evidence change has committed its revision.')
    policy = SnapshotPolicy.model_validate_json(json.dumps(incident.policy))
    facts = {(fact.subject_id, fact.field): fact for fact in CanonicalFact.objects.filter(incident=incident)}

    def project(subject_id, field):
        fact = facts.get((subject_id, field))
        if fact is None:
            return {}
        status = fact.status
        if (status in {'HUMAN_VERIFIED', 'DERIVED_FROM_VERIFIED'} and fact.observed_at
                and incident.simulated_now - fact.observed_at > timedelta(hours=policy.freshness_hours)):
            status = 'STALE'
        return dict(value=fact.value, status=status, observed_at=fact.observed_at,
                    evidence_id=fact.claim_id, source_id=fact.source_id, basis=fact.basis)

    facilities = tuple(FacilitySnapshot(id=f.id, name=f.name, daily_demand=project(f.id, 'daily_demand'))
                       for f in Facility.objects.filter(incident=incident).order_by('id'))
    batches = tuple(BatchSnapshot(
        id=batch.id, facility_id=batch.facility_id, facility_name=batch.facility.name,
        product_id=batch.product_id, product_code=batch.product.code, base_unit=batch.product.base_unit,
        batch_code=batch.batch_code,
        **{field: project(batch.id, field) for field in ('on_hand', 'reserved', 'protected_reserve', 'reserve_accounting', 'expires_on', 'storage_usable')},
    ) for batch in StockBatch.objects.filter(incident=incident).select_related('facility', 'product').order_by('id'))
    request = SupplyRequest.objects.filter(incident=incident).first()
    request_snapshot = None if request is None else RequestSnapshot(
        id=request.id, facility_id=request.facility_id, product_id=request.product_id,
        **{field: project(request.id, field) for field in ('quantity', 'is_net_transfer_request', 'need_by')},
    )
    routes = tuple(RouteSnapshot(
        id=route.id, origin_id=route.origin_id, origin_name=route.origin.name, destination_id=route.destination_id,
        status=project(route.id, 'route_status'), verified_open_eta_hours=project(route.id, 'verified_open_eta_hours'),
    ) for route in RouteObservation.objects.filter(incident=incident).select_related('origin').order_by('id'))
    claims = tuple(ClaimSnapshot(
        id=claim.id, subject_id=claim.subject_id, field=claim.field, value=claim.value, status=claim.status,
        observed_at=claim.observed_at, source_id=claim.source_id, span_id=claim.span_id, basis=claim.basis,
    ) for claim in InventoryClaim.objects.filter(incident=incident).order_by('id'))
    return IncidentSnapshot(incident_id=incident.id, revision=incident.revision, as_of=incident.simulated_now,
                            request=request_snapshot, batches=batches, facilities=facilities, routes=routes,
                            claims=claims, policy=policy,
                            source_ids=tuple(SourceDocument.objects.filter(incident=incident).order_by('id').values_list('id', flat=True)))
