from uuid import uuid4
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


class FrozenQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Frozen planning records cannot be changed.')

    def delete(self):
        raise ValidationError('Frozen planning records cannot be deleted.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Frozen planning records cannot be changed.')


class FrozenRecord(models.Model):
    objects = FrozenQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Frozen planning records cannot be changed.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Frozen planning records cannot be deleted.')


class PlanRun(FrozenRecord):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    incident = models.ForeignKey('healthmesh.Incident', on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    revision = models.PositiveIntegerField()
    snapshot_id = models.CharField(max_length=64)
    input_snapshot = models.JSONField()
    result = models.JSONField()


class PlanProposal(FrozenRecord):
    """One atomic candidate; allocation legs are reviewed together."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    run = models.ForeignKey(PlanRun, on_delete=models.PROTECT, related_name='proposals')
    position = models.PositiveIntegerField()
    branch = models.JSONField()

    class Meta(FrozenRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['run', 'position'], name='unique_run_branch_position')]
