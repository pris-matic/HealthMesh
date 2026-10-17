from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(django_user_model):
    actor = django_user_model.objects.create_user('amd-coordinator')
    actor.groups.add(Group.objects.create(name='coordinator'))
    client = Client()
    client.force_login(actor)
    return client


def test_missing_credentials_reports_unavailable(client, settings):
    from healthmesh.ai_gateway.service import get_amd_adapter
    from healthmesh.models import AIInferenceReceipt, InventoryClaim
    settings.AMD_API_KEY = settings.AMD_API_BASE_URL = settings.AMD_MODEL_ID = ''
    response = client.get('/api/v1/amd/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'UNAVAILABLE', 'reason': 'missing_configuration'}
    result = get_amd_adapter().extract_claims('SYNTHETIC 80 boxes', (), 'v1')
    assert result.status == 'UNAVAILABLE'
    assert result.claims == ()
    assert result.receipt is None
    assert not AIInferenceReceipt.objects.exists()
    assert not InventoryClaim.objects.exists()


def test_configured_but_unverified_stays_unavailable_without_leaking_secret(client, settings):
    from healthmesh.ai_gateway.service import get_amd_adapter
    settings.AMD_API_KEY = 'private-api-key'
    settings.AMD_API_BASE_URL = 'https://unverified.example.test/private'
    settings.AMD_MODEL_ID = 'unverified-model'
    response = client.get('/api/v1/amd/health')
    assert response.json() == {'status': 'UNAVAILABLE', 'reason': 'provider_transport_unverified'}
    assert b'private' not in response.content
    adapter = get_amd_adapter()
    for result in (adapter.explain_conflict(uuid4(), ()), adapter.draft_verification({}, ())):
        assert result.status == 'UNAVAILABLE'
        assert result.receipt is None
        assert result.text is None
        assert result.cited_span_ids == ()


def test_health_requires_demo_role():
    assert Client().get('/api/v1/amd/health').status_code == 401


def test_receipt_redacts_secret_and_prompt():
    from healthmesh.ai_gateway.service import record_receipt
    from healthmesh.ai_gateway.types import ReceiptMetadata
    from healthmesh.models import AIInferenceReceipt
    now = timezone.now()
    metadata = dict(provider='AMD', model_id='fixture-model', timestamp=now,
                    latency_ms=42, status='INVALID_OUTPUT', request_id='fixture-request',
                    input_schema_version='v1', output_schema_version='v1')
    # A completed-call fixture exercises metadata storage; this is not a provider call.
    receipt = record_receipt(ReceiptMetadata(**metadata))
    stored = AIInferenceReceipt.objects.values().get(pk=receipt.id)
    for name, value in metadata.items():
        assert stored[name] == value
    assert stored['timestamp'].utcoffset() == timedelta(0)
    assert not {'api_key', 'prompt', 'raw_response', 'headers'} & set(stored)
    with pytest.raises(ValidationError):
        ReceiptMetadata(**metadata, api_key='private-api-key', prompt='full synthetic prompt')
    with pytest.raises(ValidationError):
        ReceiptMetadata(**{**metadata, 'status': 'UNAVAILABLE'})
    assert AIInferenceReceipt.objects.count() == 1


def test_timeout_does_not_advance_incident(django_user_model, monkeypatch):
    from healthmesh.ai_gateway import service
    from healthmesh.models import AIInferenceReceipt, CanonicalFact, InventoryClaim
    from healthmesh.scenarios.fixture import load_scenario
    from healthmesh.scenarios.service import create_incident
    actor = django_user_model.objects.create_user('timeout-actor')
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    before = (incident.revision, InventoryClaim.objects.count(), list(CanonicalFact.objects.values()))

    class TimedOutProvider:
        def extract_claims(self, text, allowed_spans, schema_version):
            raise TimeoutError('upstream-private-key-and-prompt')

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: TimedOutProvider())
    result = service.extract_claims('SYNTHETIC 80 boxes', (), 'v1')
    assert result.status == 'TIMEOUT'
    assert result.receipt is None
    assert result.claims == ()
    assert 'private' not in result.model_dump_json()
    incident.refresh_from_db()
    assert (incident.revision, InventoryClaim.objects.count(), list(CanonicalFact.objects.values())) == before
    assert not AIInferenceReceipt.objects.exists()


def test_input_limits_and_schema_reject_without_receipts():
    from healthmesh.ai_gateway.service import get_amd_adapter
    adapter = get_amd_adapter()
    for text, spans, schema in [('x' * 65537, (), 'v1'), ('SYNTHETIC', (None,) * 129, 'v1'),
                                ('SYNTHETIC', (), 'unknown')]:
        result = adapter.extract_claims(text, spans, schema)
        assert result.status == 'INVALID_OUTPUT'
        assert result.receipt is None
        assert result.claims == ()


def test_service_blocks_unsafe_or_unbounded_span_payload_before_adapter(monkeypatch):
    from types import SimpleNamespace
    from healthmesh.ai_gateway import service

    class MustNotReceiveUnsafeInput:
        def extract_claims(self, *args):
            pytest.fail('Unsafe or unbounded input reached the provider boundary')

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: MustNotReceiveUnsafeInput())
    text = 'SYNTHETIC email demo@example.test'
    raw_span = SimpleNamespace(start=0, end=len(text), text=text)
    for spans, schema in [((raw_span,), 'v1'), ((None,) * 129, 'v1'), ((), 'unknown')]:
        result = service.extract_claims(text, spans, schema)
        assert result.status == 'INVALID_OUTPUT'
        assert result.receipt is None
