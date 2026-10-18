"""Seed a synthetic incident from signed facts, preserving raw text as evidence only."""
import json
from datetime import datetime
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from healthmesh.models import CanonicalFact, Facility, Incident, InventoryClaim, Product, RouteObservation, SourceDocument, SourceSpan, StockBatch, SupplyRequest
from healthmesh.db_models.core import incident_mutation
from healthmesh.sources.spans import line_spans
from .fixture import ScenarioSeed


def _signed_field_span(source, subject, field, value):
    """Cite the exact field/value inside this subject's signed JSON object."""
    text = source.decoded_text
    identity_field = 'batch_id' if isinstance(subject, StockBatch) else 'id'
    identity = f'{json.dumps(identity_field)}: {json.dumps(str(subject.fixture_id))}'
    if text.count(identity) != 1:
        raise ValidationError('Signed source has no unique typed subject anchor.')
    identity_start = text.index(identity)
    object_starts = []
    quoted = escaped = False
    for index, char in enumerate(text[:identity_start]):
        if escaped:
            escaped = False
        elif char == '\\' and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == '{':
            object_starts.append(index)
        elif not quoted and char == '}':
            object_starts.pop()
    if not object_starts:
        raise ValidationError('Signed source subject object is malformed.')
    start = object_starts[-1]
    subject_object, length = json.JSONDecoder().raw_decode(text[start:])
    if subject_object.get(identity_field) != str(subject.fixture_id):
        raise ValidationError('Signed source subject identity does not match the claim.')
    source_field = 'status' if field == 'route_status' else field
    source_value = subject_object.get(source_field)
    same_value = (datetime.fromisoformat(source_value) == datetime.fromisoformat(value)
                  if field == 'need_by' else source_value == value)
    if not same_value:
        raise ValidationError('Signed source value does not match the claim.')
    anchor = f'{json.dumps(source_field)}: {json.dumps(source_value, sort_keys=True)}'
    subject_text = text[start:start + length]
    if subject_text.count(anchor) != 1:
        raise ValidationError('Signed source has no unique field/value anchor for the claim.')
    field_start = start + subject_text.index(anchor)
    return SourceSpan.objects.create(incident=source.incident, source=source,
                                     start=field_start, end=field_start + len(anchor))


@transaction.atomic
def create_incident(seed: ScenarioSeed, actor) -> Incident:
    if not actor or not actor.pk or not actor.is_authenticated or not actor.is_active:
        raise ValidationError('An active synthetic demo actor is required.')
    facts = seed.public_facts
    facility_ids = {facility.id: uuid4() for facility in facts.facilities}
    policy = seed.policy.model_dump(mode='json')
    for task in policy['verification_tasks']:
        task['facility_id'] = str(facility_ids[UUID(task['facility_id'])])
    incident = Incident.objects.create(scenario_id=seed.scenario_id, fixture_revision=seed.fixture_revision,
                                       synthetic=seed.synthetic, simulated_now=facts.as_of,
                                       policy=policy, created_by=actor)
    with incident_mutation(incident.id):
        _seed_records(seed, incident, facility_ids)
    incident.refresh_from_db()
    return incident


def _seed_records(seed, incident, facility_ids):
    facts = seed.public_facts
    facilities = {}
    for facility in facts.facilities:
        facilities[facility.id] = Facility.objects.create(
            id=facility_ids[facility.id], incident=incident, fixture_id=facility.id, name=facility.name)
    brook = next(facilities[f.id] for f in facts.facilities if f.name == 'Brook')
    sources = {}
    for reference in seed.source_references:
        if reference.kind == 'RAW_TEXT':
            if reference.text is None:
                raise ValidationError('Load and validate source bytes before creating an incident.')
            original = reference.text.encode('utf-8')
            decoded = reference.text  # Synthetic fixture contains no protected identifiers.
        else:
            decoded = json.dumps({'source_id': str(reference.id), 'public_facts': facts.model_dump(mode='json')}, sort_keys=True)
            original = decoded.encode('utf-8')
        sources[reference.id] = SourceDocument.objects.create(
            incident=incident, facility=brook if reference.kind == 'RAW_TEXT' else None,
            fixture_id=reference.id, kind=reference.kind, observed_at=reference.observed_at,
            verified=reference.verified, filename=reference.path or '', original_blob=original,
            decoded_text=decoded, sha256=reference.sha256 or '',
        )
        if reference.kind == 'RAW_TEXT':
            for draft in line_spans(decoded):
                SourceSpan.objects.create(incident=incident, source=sources[reference.id],
                                          start=draft.start, end=draft.end)
    product = Product.objects.create(incident=incident, fixture_id=facts.product.id,
                                     code=facts.product.code, base_unit=facts.product.base_unit)
    def seed_fact(subject, field, value, source_id, observed_at, status='HUMAN_VERIFIED'):
        source = sources[source_id]
        basis = 'Signed synthetic fixture; not an inferred text claim'
        span = _signed_field_span(source, subject, field, value)
        claim = InventoryClaim.objects.create(incident=incident, subject_id=subject.id, field=field, value=value,
                                               source=source, span=span, observed_at=observed_at,
                                               status='HUMAN_VERIFIED', basis=basis)
        CanonicalFact.objects.create(incident=incident, subject_id=subject.id, field=field, value=value,
                                      source=source, claim=claim, observed_at=observed_at, status=status, basis=basis)

    for facility in facts.facilities:
        row = facilities[facility.id]
        seed_fact(row, 'daily_demand', [day.model_dump(mode='json') for day in facility.daily_demand],
                  facility.demand_source_id, sources[facility.demand_source_id].observed_at)
        stock = facility.stock
        batch = StockBatch.objects.create(incident=incident, fixture_id=stock.batch_id,
                                           facility=row, product=product, batch_code=stock.batch_code)
        seed_fact(batch, 'on_hand', stock.on_hand, stock.count_source_id, stock.count_observed_at)
        for field in ('reserved', 'protected_reserve', 'reserve_accounting', 'expires_on', 'storage_usable'):
            value = getattr(stock, field)
            seed_fact(batch, field, value.isoformat() if field == 'expires_on' else value,
                      stock.current_facts_source_id, stock.observed_at)
    request = SupplyRequest.objects.create(incident=incident, fixture_id=facts.request.id,
                                           facility=facilities[facts.request.facility_id], product=product)
    for field in ('quantity', 'is_net_transfer_request', 'need_by'):
        value = getattr(facts.request, field)
        seed_fact(request, field, value.isoformat() if field == 'need_by' else value,
                  facts.request.source_id, sources[facts.request.source_id].observed_at)
    for route in facts.routes:
        row = RouteObservation.objects.create(incident=incident, fixture_id=route.id,
                                              origin=facilities[route.origin_id], destination=facilities[route.destination_id])
        seed_fact(row, 'route_status', route.status, route.source_id, route.observed_at,
                  'UNKNOWN' if route.status == 'UNKNOWN' else 'HUMAN_VERIFIED')
        seed_fact(row, 'verified_open_eta_hours', route.verified_open_eta_hours, route.source_id, route.observed_at)
