"""Genuine Token Factory acceptance. A skip is not acceptance."""
import os

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from healthmesh.models import (AIExtractionRun, AIInferenceReceipt, CanonicalFact,
                               InventoryClaim, SourceDocument)
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
    assert {(claim.field, claim.value) for claim in claims} >= {('on_hand', 80), ('reserved', 25)}
    assert all(claim.status == 'OBSERVED_UNVERIFIED' and claim.source_id == source.id
               and source.decoded_text[claim.span.start:claim.span.end] == claim.span.text for claim in claims)
    assert list(CanonicalFact.objects.filter(incident=incident).values()) == signed_facts
    stored = AIInferenceReceipt.objects.values().get(pk=receipt.id)
    assert not {'api_key', 'prompt', 'raw_content', 'raw_response', 'source_id'} & set(stored)
    assert source.decoded_text not in str(stored)
    assert source.decoded_text not in response.content.decode()
