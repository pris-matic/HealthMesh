"""Append-only answers to selected synthetic verification questions."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .sources import ImmutableEvidence


class VerificationResponse(ImmutableEvidence):
    task = models.OneToOneField('healthmesh.VerificationTask', on_delete=models.PROTECT,
                                related_name='response')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=[(value, value) for value in (
        'CONFIRMED', 'REJECTED', 'UNRESOLVED')])
    value = models.JSONField(null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    claimed_source = models.CharField(max_length=200)
    observed_at = models.DateTimeField()
    timely = models.BooleanField()
    claim = models.ForeignKey('healthmesh.InventoryClaim', on_delete=models.PROTECT,
                              null=True, blank=True)

    def clean(self):
        super().clean()
        if not self.claimed_source.strip():
            raise ValidationError({'claimed_source': 'A named human source is required.'})
        if (self.status == 'CONFIRMED' and self.timely) != bool(self.claim_id):
            raise ValidationError({'claim': 'Only a timely confirmation can have a verified claim.'})
        if self.status != 'CONFIRMED' and (self.value is not None or self.unit):
            raise ValidationError({'value': 'Only a confirmation supplies a value and unit.'})
        if self.claim_id and (self.claim.subject_id != self.task.subject_id or
                              self.claim.field != self.task.field or
                              self.claim.value != self.value or
                              self.claim.status != 'HUMAN_VERIFIED'):
            raise ValidationError({'claim': 'Response claim must match the verified answer.'})

    class Meta(ImmutableEvidence.Meta):
        constraints = [models.CheckConstraint(
            condition=models.Q(status__in=['CONFIRMED', 'REJECTED', 'UNRESOLVED']),
            name='verification_response_known_status')]


class HumanAttestation(ImmutableEvidence):
    """An explicit claim review or conflict resolution with a new manual claim."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=24, choices=[(value, value) for value in (
        'CLAIM_REVIEW', 'CONFLICT_RESOLUTION')])
    target_claim = models.ForeignKey('healthmesh.InventoryClaim', on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='human_reviews')
    target_conflict = models.ForeignKey('healthmesh.EvidenceConflict', on_delete=models.PROTECT,
                                        null=True, blank=True, related_name='human_resolutions')
    claim = models.OneToOneField('healthmesh.InventoryClaim', on_delete=models.PROTECT,
                                 related_name='human_attestation')
    claimed_source = models.CharField(max_length=200)
    observed_at = models.DateTimeField()

    def clean(self):
        super().clean()
        if not self.claimed_source.strip():
            raise ValidationError({'claimed_source': 'A named human source is required.'})
        if ((self.action == 'CLAIM_REVIEW') != bool(self.target_claim_id) or
                (self.action == 'CONFLICT_RESOLUTION') != bool(self.target_conflict_id)):
            raise ValidationError('Attestation action must name one matching target.')
        target = self.target_claim if self.target_claim_id else self.target_conflict
        if (self.claim.status != 'HUMAN_VERIFIED' or
                (self.claim.subject_id, self.claim.field) != (target.subject_id, target.field)):
            raise ValidationError({'claim': 'Attestation must cite a matching new verified claim.'})

    class Meta(ImmutableEvidence.Meta):
        constraints = [models.CheckConstraint(
            condition=(models.Q(action='CLAIM_REVIEW', target_claim__isnull=False,
                                target_conflict__isnull=True) |
                       models.Q(action='CONFLICT_RESOLUTION', target_claim__isnull=True,
                                target_conflict__isnull=False)),
            name='human_attestation_one_target')]
