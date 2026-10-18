"""Database-backed admission for the team's shared Token Factory rate limit.

The singleton row is acquired with one conditional UPDATE, so workers cannot
rely on process-local locks or SQLite's ineffective select_for_update. The
HTTP call runs after the UPDATE commits, outside a database transaction.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
import math
import time
from uuid import uuid4

from django.db import DatabaseError
from django.db.models import F
from django.db.models.functions import Greatest, Now
from django.utils import timezone

from healthmesh.models import AMDCallGate


SCOPE = 'token_factory'
CALL_DEADLINE_SECONDS = 10
LEASE_SECONDS = 30  # Longer than the entire bounded call, including cooldown.
COOLDOWN_SECONDS = 2
_active_deadline = ContextVar('healthmesh_amd_call_deadline', default=None)
_active_owner = ContextVar('healthmesh_amd_call_owner', default=None)


def current_call_deadline():
    return _active_deadline.get()


def acquire_call(deadline):
    """Acquire one cross-worker lease or fail closed when busy/unavailable."""
    while time.monotonic() < deadline:
        owner = uuid4()
        try:
            updated = AMDCallGate.objects.filter(
                pk=SCOPE, lease_until__lte=Now(), next_allowed_at__lte=Now()
            ).update(owner_token=owner, lease_until=Now() + timedelta(seconds=LEASE_SECONDS))
            if updated == 1:
                return owner
            gate = AMDCallGate.objects.get(pk=SCOPE)
        except (DatabaseError, AMDCallGate.DoesNotExist):
            return None

        now = timezone.now()
        if gate.lease_until > now:
            return None  # Another worker owns the active call.
        wait_seconds = max(0.01, (gate.next_allowed_at - now).total_seconds())
        remaining = deadline - time.monotonic()
        if wait_seconds >= remaining:
            return None
        time.sleep(min(wait_seconds, remaining, 0.25))
    return None


def release_call(owner):
    """Only the current owner can end its lease; retain any 429 floor."""
    try:
        AMDCallGate.objects.filter(pk=SCOPE, owner_token=owner).update(
            owner_token=None, lease_until=Now(),
            next_allowed_at=Greatest(F('next_allowed_at'),
                                     Now() + timedelta(seconds=COOLDOWN_SECONDS)))
    except DatabaseError:
        # Fail closed until the longer lease expires if storage is unavailable.
        pass


def defer_after_429(seconds):
    """Share Retry-After across workers, including when this call stops retrying."""
    if _active_owner.get() is None or not isinstance(seconds, (int, float)):
        return
    if not math.isfinite(seconds):
        seconds = 7 * 86400
    seconds = min(max(0.0, seconds), 7 * 86400)
    try:
        AMDCallGate.objects.filter(pk=SCOPE, owner_token=_active_owner.get()).update(
            next_allowed_at=Greatest(F('next_allowed_at'),
                                     Now() + timedelta(seconds=seconds)))
    except DatabaseError:
        # The current lease still prevents immediate admission on DB failure.
        pass


@contextmanager
def admitted_call():
    """Reserve a shared call slot and expose its deadline to the transport."""
    deadline = time.monotonic() + CALL_DEADLINE_SECONDS
    owner = acquire_call(deadline)
    if owner is None:
        yield False
        return
    deadline_token = _active_deadline.set(deadline)
    owner_token = _active_owner.set(owner)
    try:
        yield True
    finally:
        _active_owner.reset(owner_token)
        _active_deadline.reset(deadline_token)
        release_call(owner)
