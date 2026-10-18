"""Detect value disagreements without replacing or verifying any claim."""
from collections import defaultdict
import json
from uuid import UUID

from django.db import transaction

from healthmesh.db_models.core import incident_mutation
from healthmesh.models import EvidenceConflict, Incident, InventoryClaim


def _value_key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _covered_by_resolution(row, claims):
    """A new observation agrees only when it matches the selected human value."""
    selected = next((claim for claim in claims if claim.id == row.resolution_id), None)
    if selected is None:
        return False
    reviewed_ids = set(row.claim_ids)
    selected_value = _value_key(selected.value)
    return all(str(claim.id) in reviewed_ids or _value_key(claim.value) == selected_value
               for claim in claims)


@transaction.atomic
def detect_conflicts(incident_id: UUID) -> list[EvidenceConflict]:
    """Persist changed field conflicts once; a repeated scan is read only."""
    incident = Incident.objects.select_for_update().get(pk=incident_id)
    claims_by_field = defaultdict(list)
    for claim in InventoryClaim.objects.filter(incident=incident).order_by('id'):
        claims_by_field[(claim.subject_id, claim.field)].append(claim)
    existing = list(EvidenceConflict.objects.filter(incident=incident).order_by('created_at', 'id'))
    open_by_field = {(row.subject_id, row.field): row for row in existing if row.state == 'OPEN'}
    resolved_by_field = defaultdict(list)
    for row in existing:
        if row.state == 'RESOLVED':
            resolved_by_field[(row.subject_id, row.field)].append(row)

    changes = []
    for key, claims in claims_by_field.items():
        if len({_value_key(claim.value) for claim in claims}) < 2:
            continue
        claim_ids = sorted(str(claim.id) for claim in claims)
        active = open_by_field.get(key)
        if active:
            if active.claim_ids != claim_ids:
                changes.append(('update', active, claim_ids))
        elif not (resolved_by_field[key] and
                  _covered_by_resolution(resolved_by_field[key][-1], claims)):
            changes.append(('create', key, claim_ids))

    if changes:
        with incident_mutation(incident.id):
            for kind, key_or_row, claim_ids in changes:
                if kind == 'update':
                    key_or_row.claim_ids = claim_ids
                    key_or_row.save()
                else:
                    subject_id, field = key_or_row
                    EvidenceConflict.objects.create(incident=incident, subject_id=subject_id,
                        field=field, field_key=f'{subject_id}:{field}', claim_ids=claim_ids,
                        conflict_type='VALUE_DISAGREEMENT', state='OPEN')
    return list(EvidenceConflict.objects.filter(incident=incident, state='OPEN').order_by('field_key'))
