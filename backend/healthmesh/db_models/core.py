"""Canonical identities and state; evidence records live in sources.py."""
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid4

from django.conf import settings
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


STATUSES = tuple((value, value) for value in (
    'OBSERVED_UNVERIFIED', 'CONFLICTED', 'HUMAN_VERIFIED',
    'DERIVED_FROM_VERIFIED', 'UNKNOWN', 'STALE',
))
VERIFIED = {'HUMAN_VERIFIED', 'DERIVED_FROM_VERIFIED'}
QUANTITIES = {'on_hand', 'reserved', 'protected_reserve', 'quantity', 'verified_open_eta_hours'}
_active_mutations = ContextVar('healthmesh_incident_mutations', default=frozenset())


def mutation_in_progress(incident_id):
    return incident_id in _active_mutations.get()


@contextmanager
def incident_mutation(incident_id):
    """One logical evidence change, one locked transaction, one revision.

    Nested boundaries for the same incident coalesce. Build snapshots only after
    leaving the boundary. A failure rolls back the records and revision together.
    Instance writes outside a boundary still advance their own revision.
    """
    incident_id = UUID(str(incident_id))
    with transaction.atomic():
        Incident.objects.select_for_update().get(pk=incident_id)
        if mutation_in_progress(incident_id):
            yield
            return
        token = _active_mutations.set(_active_mutations.get() | {incident_id})
        try:
            yield
            bump_revision(incident_id)
        finally:
            _active_mutations.reset(token)


def validate_value(field, value):
    if field in QUANTITIES and (type(value) is not int or value < 0):
        raise ValidationError({ 'value': 'Quantity must be a nonnegative integer.' })
    if field in {'quantity', 'verified_open_eta_hours'} and value == 0:
        raise ValidationError({'value': 'Quantity must be positive.'})
    if field == 'reserve_accounting' and value != 'separate':
        raise ValidationError({'value': 'Reserved stock excludes protected reserve: separate required.'})
    if field == 'storage_usable' and type(value) is not bool:
        raise ValidationError({'value': 'Storage usability requires a boolean.'})
    if field == 'is_net_transfer_request' and value is not True:
        raise ValidationError({'value': 'Only net transfer requests are supported.'})
    if field == 'route_status' and value not in {'OPEN', 'CLOSED', 'UNKNOWN'}:
        raise ValidationError({'value': 'Unknown route status.'})
    if field in {'need_by', 'expires_on'}:
        try:
            parsed = datetime.fromisoformat(value) if field == 'need_by' else date.fromisoformat(value)
            if field == 'need_by' and (parsed.tzinfo is None or parsed.utcoffset() != timedelta(0)):
                raise ValueError('UTC required')
        except (TypeError, ValueError):
            raise ValidationError({'value': 'A valid ISO date or timezone-aware UTC deadline is required.'})


def validate_subject(incident_id, subject_id, field):
    names = {
        'on_hand': 'StockBatch', 'reserved': 'StockBatch', 'protected_reserve': 'StockBatch',
        'reserve_accounting': 'StockBatch', 'expires_on': 'StockBatch', 'storage_usable': 'StockBatch',
        'daily_demand': 'Facility', 'quantity': 'SupplyRequest', 'need_by': 'SupplyRequest',
        'is_net_transfer_request': 'SupplyRequest', 'route_status': 'RouteObservation',
        'verified_open_eta_hours': 'RouteObservation',
    }
    model_names = [names[field]] if field in names else ['StockBatch', 'Facility', 'SupplyRequest', 'RouteObservation']
    if not any(apps.get_model('healthmesh', name).objects.filter(pk=subject_id, incident_id=incident_id).exists()
               for name in model_names):
        raise ValidationError({'subject_id': 'A matching subject in the same incident is required.'})


class GuardedQuerySet(models.QuerySet):
    """Bulk writes bypass instance validation and revision locking; reject them."""
    def update(self, **kwargs):
        raise ValidationError('Use a validated instance write to advance the incident revision.')

    def delete(self):
        raise ValidationError('Use a validated instance delete; evidence is append-only.')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Bulk creation bypasses provenance and revision validation.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Bulk updates bypass provenance and revision validation.')


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    objects = GuardedQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def clean(self):
        super().clean()
        for field in self._meta.fields:
            value = getattr(self, field.attname)
            if isinstance(field, models.DateTimeField) and value is not None:
                if timezone.is_naive(value) or value.utcoffset() != timedelta(0):
                    raise ValidationError({field.name: 'Timezone-aware UTC required.'})


def bump_revision(incident_id):
    # Deliberately private raw QuerySet: all public bulk writes are guarded.
    models.QuerySet(model=Incident).filter(pk=incident_id).update(revision=F('revision') + 1)


class Incident(Record):
    scenario_id = models.CharField(max_length=80)
    fixture_revision = models.PositiveIntegerField()
    synthetic = models.BooleanField(default=True)
    simulated_now = models.DateTimeField()
    revision = models.PositiveBigIntegerField(default=0, editable=False)
    policy = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta(Record.Meta):
        constraints = [models.CheckConstraint(condition=Q(synthetic=True), name='incident_synthetic_only')]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old = type(self).objects.select_for_update().filter(pk=self.pk).first()
            if old:
                if self.simulated_now < old.simulated_now:
                    raise ValidationError('The simulated clock cannot move backwards.')
                self.revision = old.revision if mutation_in_progress(self.pk) else old.revision + 1
            elif self.revision != 0:
                raise ValidationError('A new incident starts at revision zero.')
            self.full_clean()
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'revision'}
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Incident history cannot be deleted.')


class IncidentRecord(Record):
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT)

    class Meta(Record.Meta):
        abstract = True

    def clean(self):
        super().clean()
        for field in self._meta.fields:
            if isinstance(field, models.ForeignKey) and field.name != 'incident' and getattr(self, field.attname):
                related = getattr(self, field.name)
                if hasattr(related, 'incident_id') and related.incident_id != self.incident_id:
                    raise ValidationError({field.name: 'Evidence and subject must belong to the same incident.'})
        if getattr(self, 'observed_at', None) and self.observed_at > self.incident.simulated_now:
            raise ValidationError({'observed_at': 'Future observations are not accepted.'})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Incident.objects.select_for_update().get(pk=self.incident_id)
            old = type(self).objects.filter(pk=self.pk).first()
            if old and old.incident_id != self.incident_id:
                raise ValidationError('An existing record cannot change incidents.')
            if old and getattr(self, 'append_only', False):
                raise ValidationError('Evidence is append-only; create a new record.')
            self.full_clean()
            result = super().save(*args, **kwargs)
            if not mutation_in_progress(self.incident_id):
                bump_revision(self.incident_id)
            return result

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            Incident.objects.select_for_update().get(pk=self.incident_id)
            incident_id = self.incident_id
            result = super().delete(*args, **kwargs)
            if not mutation_in_progress(incident_id):
                bump_revision(incident_id)
            return result


class Facility(IncidentRecord):
    fixture_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=80)

    class Meta(IncidentRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['incident', 'name'], name='unique_incident_facility')]


class Product(IncidentRecord):
    fixture_id = models.UUIDField(null=True, blank=True)
    code = models.CharField(max_length=40, default='SIM-MX-01')
    base_unit = models.CharField(max_length=20, default='box')

    class Meta(IncidentRecord.Meta):
        constraints = [
            models.UniqueConstraint(fields=['incident', 'code'], name='unique_incident_product'),
            models.CheckConstraint(condition=Q(base_unit='box', code='SIM-MX-01'), name='synthetic_product_unit'),
        ]


class StockBatch(IncidentRecord):
    fixture_id = models.UUIDField(null=True, blank=True)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch_code = models.CharField(max_length=80)

    class Meta(IncidentRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['incident', 'facility', 'product', 'batch_code'], name='unique_stock_identity')]


class SupplyRequest(IncidentRecord):
    fixture_id = models.UUIDField(null=True, blank=True)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)

    class Meta(IncidentRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['incident'], name='one_incident_request')]


class RouteObservation(IncidentRecord):
    fixture_id = models.UUIDField(null=True, blank=True)
    origin = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='outgoing_routes')
    destination = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='incoming_routes')

    class Meta(IncidentRecord.Meta):
        constraints = [
            models.UniqueConstraint(fields=['incident', 'origin', 'destination'], name='unique_incident_route'),
            models.CheckConstraint(condition=~Q(origin=F('destination')), name='route_distinct_endpoints'),
        ]


class CanonicalFact(IncidentRecord):
    subject_id = models.UUIDField()
    field = models.CharField(max_length=80)
    value = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUSES)
    observed_at = models.DateTimeField(null=True, blank=True)
    source = models.ForeignKey('healthmesh.SourceDocument', on_delete=models.PROTECT, null=True, blank=True)
    claim = models.ForeignKey('healthmesh.InventoryClaim', on_delete=models.PROTECT, null=True, blank=True)
    basis = models.CharField(max_length=200)

    class Meta(IncidentRecord.Meta):
        constraints = [
            models.UniqueConstraint(fields=['incident', 'subject_id', 'field'], name='unique_canonical_field'),
            models.CheckConstraint(condition=Q(status__in=[v[0] for v in STATUSES]), name='canonical_known_status'),
            models.CheckConstraint(condition=~Q(field__in=sorted(QUANTITIES)) | Q(value__gte=0), name='canonical_nonnegative_quantity'),
            models.CheckConstraint(condition=~Q(field='reserve_accounting') | Q(value='separate'), name='canonical_separate_reserve'),
        ]

    def clean(self):
        super().clean()
        validate_subject(self.incident_id, self.subject_id, self.field)
        if self.status == 'UNKNOWN' and self.value is None:
            return
        validate_value(self.field, self.value)
        if not self.claim_id or not self.source_id or not self.observed_at:
            raise ValidationError('Known canonical values require a supporting claim, source and time.')
        claim = self.claim
        if (claim.subject_id, claim.field, claim.value, claim.source_id, claim.observed_at) != (
            self.subject_id, self.field, self.value, self.source_id, self.observed_at
        ):
            raise ValidationError('Canonical value must match its supporting claim exactly.')
        if self.status in VERIFIED and (claim.status not in VERIFIED or not self.source.verified):
            raise ValidationError('Unsigned evidence cannot become verified canonical stock.')
