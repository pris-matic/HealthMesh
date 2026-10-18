"""Persistent field conflicts; claim and source histories remain append only."""
import json
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .core import IncidentRecord


class EvidenceConflict(IncidentRecord):
    subject_id = models.UUIDField()
    field = models.CharField(max_length=80)
    field_key = models.CharField(max_length=120)
    claim_ids = models.JSONField(default=list)
    conflict_type = models.CharField(max_length=30, choices=[('VALUE_DISAGREEMENT', 'VALUE_DISAGREEMENT')])
    state = models.CharField(max_length=20, choices=[('OPEN', 'OPEN'), ('RESOLVED', 'RESOLVED')])
    resolution_id = models.UUIDField(null=True, blank=True)

    class Meta(IncidentRecord.Meta):
        constraints = [
            models.UniqueConstraint(fields=['incident', 'field_key'], condition=Q(state='OPEN'),
                                    name='one_open_conflict_per_field'),
            models.CheckConstraint(condition=Q(state__in=['OPEN', 'RESOLVED']), name='conflict_known_state'),
            models.CheckConstraint(condition=Q(conflict_type='VALUE_DISAGREEMENT'), name='conflict_known_type'),
        ]

    def clean(self):
        super().clean()
        if self.field_key != f'{self.subject_id}:{self.field}':
            raise ValidationError({'field_key': 'Conflict key must name its subject and field.'})
        if type(self.claim_ids) is not list or len(self.claim_ids) < 2:
            raise ValidationError({'claim_ids': 'A conflict requires at least two claim IDs.'})
        try:
            ids = [UUID(value) for value in self.claim_ids]
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({'claim_ids': 'Claim IDs must be UUID strings.'})
        if len(set(ids)) != len(ids):
            raise ValidationError({'claim_ids': 'Claim IDs must be distinct.'})
        from healthmesh.models import InventoryClaim
        claims = list(InventoryClaim.objects.filter(id__in=ids, incident_id=self.incident_id,
                                                    subject_id=self.subject_id, field=self.field))
        if len(claims) != len(ids):
            raise ValidationError({'claim_ids': 'Claims must share this incident, subject and field.'})
        if len({json.dumps(claim.value, sort_keys=True, separators=(',', ':')) for claim in claims}) < 2:
            raise ValidationError({'claim_ids': 'Equivalent values do not form a conflict.'})
        if self.state == 'OPEN' and self.resolution_id is not None:
            raise ValidationError({'resolution_id': 'Only a resolved conflict can have a resolution.'})
        if self.state == 'RESOLVED':
            selected = next((claim for claim in claims if claim.id == self.resolution_id), None)
            if (selected is None or selected.status != 'HUMAN_VERIFIED'
                    or selected.source.kind != 'MANUAL' or not selected.source.verified):
                raise ValidationError({'resolution_id': 'Resolution must cite an included verified human manual claim.'})
