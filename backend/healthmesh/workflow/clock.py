"""Synthetic UTC time for all incident decisions and evidence revisions."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as utc_timezone
from uuid import UUID

from django.db import transaction

from healthmesh.db_models.core import incident_mutation
from healthmesh.models import Incident


@dataclass(frozen=True)
class EvidenceRevision:
    incident_id: UUID
    revision: int
    simulated_at: datetime
    response_id: UUID | None = None
    attestation_id: UUID | None = None
    claim_id: UUID | None = None
    conflict_id: UUID | None = None
    outcome: str | None = None


def require_coordinator(actor):
    from .verification import VerificationError

    if (not actor or not actor.pk or not actor.is_authenticated or not actor.is_active
            or not actor.groups.filter(name='coordinator').exists()):
        raise VerificationError('forbidden', 'A coordinator must perform this action.', 403)


def move_simulated_time(incident: Incident, target: datetime):
    """Update a locked incident inside its logical revision boundary."""
    from .verification import VerificationError

    if target.tzinfo is None or target.utcoffset() != timedelta(0):
        raise VerificationError('invalid_time', 'Simulated time must be UTC.', 400)
    if target < incident.simulated_now:
        raise VerificationError('time_reversal', 'The simulated clock cannot move backward.', 409)
    if target > incident.simulated_now:
        incident.simulated_now = target.astimezone(utc_timezone.utc)
        incident.save()


@transaction.atomic
def advance_simulated_clock(incident_id: UUID, minutes: int, actor) -> EvidenceRevision:
    require_coordinator(actor)
    from .verification import VerificationError

    if type(minutes) is not int or minutes < 0:
        raise VerificationError('invalid_time', 'Minutes must be a nonnegative integer.', 400)
    incident = Incident.objects.select_for_update().filter(pk=incident_id, synthetic=True).first()
    if incident is None:
        raise VerificationError('not_found', 'Incident not found.', 404)
    if minutes:
        with incident_mutation(incident.id):
            move_simulated_time(incident, incident.simulated_now + timedelta(minutes=minutes))
        incident.refresh_from_db()
    return EvidenceRevision(incident_id=incident.id, revision=incident.revision,
                            simulated_at=incident.simulated_now, outcome='CLOCK_ADVANCED' if minutes else 'NO_CHANGE')
