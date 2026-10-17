"""Append-only source documents, Unicode spans and candidate evidence."""
import hashlib

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from .core import IncidentRecord, QUANTITIES, STATUSES, VERIFIED, validate_subject, validate_value


class ImmutableEvidence(IncidentRecord):
    append_only = True

    class Meta(IncidentRecord.Meta):
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding or type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Evidence is append-only; create a new record.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Evidence is append-only and cannot be deleted.')


class SourceDocument(ImmutableEvidence):
    fixture_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=30, choices=[(v, v) for v in ('SIGNED_FIXTURE', 'RAW_TEXT', 'CSV', 'MANUAL')])
    observed_at = models.DateTimeField()
    verified = models.BooleanField(default=False)
    filename = models.CharField(max_length=200, blank=True)
    original_blob = models.BinaryField(default=bytes, blank=True)
    decoded_text = models.TextField(blank=True)
    sha256 = models.CharField(max_length=64, editable=False)

    def clean(self):
        super().clean()
        if self.verified and self.kind not in {'SIGNED_FIXTURE', 'MANUAL'}:
            raise ValidationError('Uploaded sources remain unverified.')
        digest = hashlib.sha256(bytes(self.original_blob)).hexdigest()
        if self.sha256 and self.sha256 != digest:
            raise ValidationError('Original source SHA-256 mismatch.')
        self.sha256 = digest


class SourceSpan(ImmutableEvidence):
    source = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()

    class Meta(ImmutableEvidence.Meta):
        constraints = [models.CheckConstraint(condition=Q(end__gt=F('start')), name='span_half_open_nonempty')]

    @property
    def text(self):
        return self.source.decoded_text[self.start:self.end]

    def save(self, *args, **kwargs):
        if not self.incident_id:
            self.incident_id = self.source.incident_id
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.start < 0 or not self.start < self.end <= len(self.source.decoded_text):
            raise ValidationError('Span offsets must bound nonempty Unicode characters in the sanitized view.')


class InventoryClaim(ImmutableEvidence):
    subject_id = models.UUIDField()
    field = models.CharField(max_length=80)
    value = models.JSONField()
    source = models.ForeignKey(SourceDocument, on_delete=models.PROTECT)
    span = models.ForeignKey(SourceSpan, on_delete=models.PROTECT, null=True, blank=True)
    observed_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=STATUSES, default='OBSERVED_UNVERIFIED')
    basis = models.CharField(max_length=200)

    class Meta(ImmutableEvidence.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(status__in=[v[0] for v in STATUSES]), name='claim_known_status'),
            models.CheckConstraint(condition=~Q(field__in=sorted(QUANTITIES)) | Q(value__gte=0), name='claim_nonnegative_quantity'),
            models.CheckConstraint(condition=~Q(field='reserve_accounting') | Q(value='separate'), name='claim_separate_reserve'),
        ]

    def clean(self):
        super().clean()
        validate_value(self.field, self.value)
        validate_subject(self.incident_id, self.subject_id, self.field)
        if self.status in VERIFIED and not self.source.verified:
            raise ValidationError('Unsigned source claims cannot be verified.')
        if self.span_id and self.span.source_id != self.source_id:
            raise ValidationError('Span must belong to the claim source.')
