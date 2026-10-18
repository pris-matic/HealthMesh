"""Coordinator-only human evidence actions with structured failure responses."""
from django.http import JsonResponse

from healthmesh.workflow.auth import api, body, error
from .verification import (VerificationAnswer, VerificationError,
                           attest_to_evidence, respond_to_verification)


def _answer(data, *, response):
    return VerificationAnswer(
        status=data.get('status') if response else 'CONFIRMED',
        value=data.get('value'), unit=data.get('unit'),
        claimed_source=data.get('claimed_source', ''),
        expected_revision=data.get('expected_revision'))


def _result(revision):
    return JsonResponse({
        'incident_id': str(revision.incident_id),
        'new_revision': revision.revision,
        'simulated_at': revision.simulated_at.isoformat(),
        'response_id': str(revision.response_id) if revision.response_id else None,
        'attestation_id': str(revision.attestation_id) if revision.attestation_id else None,
        'claim_id': str(revision.claim_id) if revision.claim_id else None,
        'conflict_id': str(revision.conflict_id) if revision.conflict_id else None,
        'outcome': revision.outcome,
        'new_plan_run_id': None,
    }, status=201)


def _failure(exc):
    return error(exc.code, str(exc), exc.http_status, exc.field_errors)


@api(['POST'], roles=('coordinator',))
def respond(request, task_id):
    try:
        revision = respond_to_verification(task_id, _answer(body(request), response=True), request.user)
    except VerificationError as exc:
        return _failure(exc)
    return _result(revision)


@api(['POST'], roles=('coordinator',))
def review_claim(request, claim_id):
    try:
        revision = attest_to_evidence('CLAIM_REVIEW', claim_id,
                                      _answer(body(request), response=False), request.user)
    except VerificationError as exc:
        return _failure(exc)
    return _result(revision)


@api(['POST'], roles=('coordinator',))
def resolve_conflict(request, conflict_id):
    try:
        revision = attest_to_evidence('CONFLICT_RESOLUTION', conflict_id,
                                      _answer(body(request), response=False), request.user)
    except VerificationError as exc:
        return _failure(exc)
    return _result(revision)
