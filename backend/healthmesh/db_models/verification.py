"""Selected synthetic verification questions are immutable and revision neutral."""
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .planning import FrozenRecord


class VerificationTask(FrozenRecord):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    incident = models.ForeignKey('healthmesh.Incident', on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    subject_id = models.UUIDField()
    field = models.CharField(max_length=30, choices=[('on_hand', 'on_hand'), ('route_status', 'route_status')])
    selected_snapshot_revision = models.PositiveBigIntegerField()
    selected_at = models.DateTimeField()
    due_at = models.DateTimeField()
    staff_minutes = models.PositiveIntegerField()
    response_delay_minutes = models.PositiveIntegerField()
    evidence_ids = models.JSONField(default=list)

    class Meta(FrozenRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['incident', 'selected_snapshot_revision', 'subject_id', 'field'],
                                                name='unique_selected_question_at_revision')]

    def clean(self):
        super().clean()
        if self.staff_minutes < 1 or self.due_at != self.selected_at + timedelta(minutes=self.response_delay_minutes):
            raise ValidationError('Verification effort and due time must match the selected public task.')
        from healthmesh.db_models.core import validate_subject
        validate_subject(self.incident_id, self.subject_id, self.field)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
