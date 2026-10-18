"""Genuine Token Factory acceptance. A skip is not acceptance."""
import os

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from healthmesh.models import (AIExtractionRun, AIInferenceReceipt, CanonicalFact,
                               InventoryClaim, SourceDocument, SourceSpan, VerificationTask)
from healthmesh.domain.snapshot import build_snapshot
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = [pytest.mark.django_db, pytest.mark.amd_live]


def test_event_endpoint_extracts_brook_note(django_user_model, settings):
    if not os.environ.get('AMD_API_KEY'):
        pytest.skip('AMD_API_KEY absent: live Token Factory acceptance remains open')
    settings.AMD_API_KEY = os.environ['AMD_API_KEY']
    settings.AMD_API_BASE_URL = os.environ.get('AMD_API_BASE_URL',
        'https://developer.amd.com.cn/radeon/api/v1')
    settings.AMD_MODEL_ID = os.environ.get('AMD_MODEL_ID', 'DeepSeek-V4-Flash')
    actor = django_user_model.objects.create_user('amd-live-coordinator')
    actor.groups.add(Group.objects.create(name='coordinator'))
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    signed_facts = list(CanonicalFact.objects.filter(incident=incident).values())
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    token = client.get('/api/v1/session').json()['csrf_token']
    response = client.post(f'/api/v1/sources/{source.id}/extract', HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'SUCCESS'
    assert payload['accepted_count'] >= 2
    assert payload['receipt_id']
    receipt = AIInferenceReceipt.objects.get(pk=payload['receipt_id'])
    assert receipt.provider == 'AMD'
    assert receipt.model_id == settings.AMD_MODEL_ID
    assert receipt.timestamp.tzinfo is not None
    assert receipt.status == 'SUCCESS'
    assert receipt.latency_ms >= 0
    assert receipt.input_schema_version == receipt.output_schema_version == 'v1'
    assert receipt.request_id is None or isinstance(receipt.request_id, str)
    run = AIExtractionRun.objects.get(receipt=receipt)
    assert run.source_id == source.id
    claims = list(InventoryClaim.objects.filter(extraction_run=run))
    observed = {(claim.field, claim.value) for claim in claims if claim.field in {'on_hand', 'reserved'}}
    assert observed == {('on_hand', 80), ('reserved', 25)}
    assert all(claim.status == 'OBSERVED_UNVERIFIED' and claim.source_id == source.id
               and source.decoded_text[claim.span.start:claim.span.end] == claim.span.text for claim in claims)
    assert list(CanonicalFact.objects.filter(incident=incident).values()) == signed_facts
    stored = AIInferenceReceipt.objects.values().get(pk=receipt.id)
    assert not {'api_key', 'prompt', 'raw_content', 'raw_response', 'source_id'} & set(stored)
    assert source.decoded_text not in str(stored)
    assert source.decoded_text not in response.content.decode()


@pytest.fixture
def grounded_case(django_user_model, settings):
    if not os.environ.get('AMD_API_KEY'):
        pytest.skip('AMD_API_KEY absent: live Token Factory grounding acceptance remains open')
    settings.AMD_API_KEY = os.environ['AMD_API_KEY']
    settings.AMD_API_BASE_URL = os.environ.get('AMD_API_BASE_URL',
        'https://developer.amd.com.cn/radeon/api/v1')
    settings.AMD_MODEL_ID = os.environ.get('AMD_MODEL_ID', 'DeepSeek-V4-Flash')
    actor = django_user_model.objects.create_user('amd-grounded-coordinator')
    actor.groups.add(Group.objects.get_or_create(name='coordinator')[0])
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    brook = next(row for row in build_snapshot(incident.id).batches if row.facility_name == 'Brook')
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    span = next(row for row in SourceSpan.objects.filter(source=source) if 'counted 80 boxes' in row.text)
    signed = CanonicalFact.objects.get(incident=incident, subject_id=brook.id, field='on_hand').claim
    candidate = InventoryClaim.objects.create(incident=incident, subject_id=brook.id,
        field='on_hand', value=80, source=source, span=span, observed_at=source.observed_at,
        status='OBSERVED_UNVERIFIED', basis='Synthetic candidate awaiting AMD wording')
    from healthmesh.evidence.conflicts import detect_conflicts
    conflict = detect_conflicts(incident.id)[0]
    incident.refresh_from_db()
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    token = client.get('/api/v1/session').json()['csrf_token']
    return incident, brook, signed, candidate, conflict, source, client, token


def _assert_grounded_receipt(payload, model_id, input_schema):
    assert payload['status'] == 'SUCCESS', payload
    assert payload['source'] == 'AMD_GENERATED_UNVERIFIED_WORDING'
    assert payload['receipt_id']
    receipt = AIInferenceReceipt.objects.get(pk=payload['receipt_id'])
    assert (receipt.provider, receipt.model_id, receipt.status) == ('AMD', model_id, 'SUCCESS')
    assert receipt.timestamp.tzinfo is not None
    assert receipt.latency_ms >= 0
    assert receipt.input_schema_version == input_schema
    assert receipt.output_schema_version == 'grounding-v1'
    assert receipt.request_id is None or isinstance(receipt.request_id, str)
    stored = AIInferenceReceipt.objects.values().get(pk=receipt.id)
    assert not {'api_key', 'prompt', 'raw_content', 'raw_response', 'source_id'} & set(stored)
    return receipt


def test_event_endpoint_explains_brook_conflict_with_two_issued_spans(grounded_case, settings):
    incident, brook, signed, candidate, conflict, source, client, token = grounded_case
    facts = list(CanonicalFact.objects.filter(incident=incident).values())
    revision = incident.revision
    response = client.post(f'/api/v1/conflicts/{conflict.id}/explain', HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200, response.content
    payload = response.json()
    _assert_grounded_receipt(payload, settings.AMD_MODEL_ID, 'conflict-v1')
    assert payload['cited_span_ids'] == [str(signed.span_id), str(candidate.span_id)]
    assert '95' in payload['text'] and '80' in payload['text']
    assert '25' not in payload['text']
    assert source.decoded_text not in response.content.decode()
    assert source.decoded_text not in str(AIInferenceReceipt.objects.values().get(pk=payload['receipt_id']))
    assert candidate.status == 'OBSERVED_UNVERIFIED'
    incident.refresh_from_db()
    assert incident.revision == revision
    assert list(CanonicalFact.objects.filter(incident=incident).values()) == facts


def test_event_endpoint_drafts_only_selected_brook_question(grounded_case, settings):
    incident, brook, signed, candidate, conflict, source, client, token = grounded_case
    import json
    selected = client.post(f'/api/v1/incidents/{incident.id}/verification-tasks',
        data=json.dumps({'subject_id': str(brook.id), 'field': 'on_hand',
                         'expected_revision': incident.revision}), content_type='application/json',
        HTTP_X_CSRFTOKEN=token)
    assert selected.status_code == 201, selected.content
    task_id = selected.json()['selected_task']['id']
    facts = list(CanonicalFact.objects.filter(incident=incident).values())
    revision = incident.revision
    response = client.post(f'/api/v1/verification-tasks/{task_id}/draft', HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200, response.content
    payload = response.json()
    _assert_grounded_receipt(payload, settings.AMD_MODEL_ID, 'question-v1')
    assert payload['cited_span_ids'] == [str(signed.span_id), str(candidate.span_id)]
    assert payload['text'].endswith('?')
    assert '25' not in payload['text']
    assert source.decoded_text not in response.content.decode()
    assert candidate.status == 'OBSERVED_UNVERIFIED'
    assert VerificationTask.objects.get(pk=task_id).field == 'on_hand'
    incident.refresh_from_db()
    assert incident.revision == revision
    assert list(CanonicalFact.objects.filter(incident=incident).values()) == facts
