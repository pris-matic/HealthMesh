"""Sanitized inference receipts are telemetry, never incident evidence mutations."""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .core import Record


class AIInferenceReceipt(Record):
    append_only = True
    provider = models.CharField(max_length=20, default='AMD', editable=False)
    model_id = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    latency_ms = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=[(v, v) for v in ('SUCCESS', 'INVALID_OUTPUT')])
    request_id = models.CharField(max_length=200, null=True, blank=True)
    input_schema_version = models.CharField(max_length=40)
    output_schema_version = models.CharField(max_length=40)

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(provider='AMD'), name='ai_receipt_amd_only'),
            models.CheckConstraint(condition=Q(status__in=['SUCCESS', 'INVALID_OUTPUT']), name='ai_receipt_completed_only'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding or type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Inference receipts are append-only.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Inference receipts are append-only.')


class AIExtractionRun(Record):
    """Business provenance for a completed call, separate from receipt telemetry."""
    source = models.ForeignKey('healthmesh.SourceDocument', on_delete=models.PROTECT)
    receipt = models.OneToOneField(AIInferenceReceipt, on_delete=models.PROTECT)
    accepted_count = models.PositiveIntegerField()
    rejected_count = models.PositiveIntegerField()

    def save(self, *args, **kwargs):
        if not self._state.adding or type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Extraction runs are append-only.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Extraction runs are append-only.')
