"""Offline contract checks. No test in this file calls AMD."""
import json
from datetime import datetime, timezone
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from healthmesh.ai_gateway.types import ExtractionResult, ReceiptMetadata
from healthmesh.models import (AIExtractionRun, AIInferenceReceipt, CanonicalFact,
                               Facility, InventoryClaim, SourceDocument, SourceSpan)
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(django_user_model):
    actor = django_user_model.objects.create_user('extract-coordinator')
    actor.groups.add(Group.objects.create(name='coordinator'))
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    brook = Facility.objects.get(incident=incident, name='Brook')
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    token = client.get('/api/v1/session').json()['csrf_token']
    return SimpleNamespace(actor=actor, incident=incident, brook=brook, source=source,
                           client=client, token=token)


def call_extract(case, source=None):
    source = source or case.source
    return case.client.post(f'/api/v1/sources/{source.id}/extract', HTTP_X_CSRFTOKEN=case.token)


def completion_content(spans, on_hand=80, reserved=25):
    stock_line = next(span for span in spans if 'counted' in span.text)
    return json.dumps({'claims': [
        {'field': 'on_hand', 'value': on_hand, 'unit': 'box', 'source_span_id': str(stock_line.id)},
        {'field': 'reserved', 'value': reserved, 'unit': 'box', 'source_span_id': str(stock_line.id)},
    ]})


def fake_completed_adapter(monkeypatch, case, content_factory=completion_content):
    from healthmesh.ai_gateway import service

    class StubAMD:
        def extract_claims(self, text, allowed_spans, schema_version):
            assert text == case.source.decoded_text
            assert schema_version == 'v1'
            assert len(allowed_spans) == 4
            assert all(span.source_id == case.source.id and text[span.start:span.end] == span.text
                       for span in allowed_spans)
            return ExtractionResult(status='SUCCESS',
                receipt=ReceiptMetadata(provider='AMD', model_id='DeepSeek-V4-Flash',
                    timestamp=datetime.now(timezone.utc), latency_ms=37, status='SUCCESS',
                    request_id='genuine-stub-header', input_schema_version='v1',
                    output_schema_version='v1'),
                raw_content=content_factory(allowed_spans))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())


def test_seeded_brook_note_is_bound_and_extraction_is_unverified(case, monkeypatch):
    assert case.source.facility_id == case.brook.id
    assert [s.text for s in SourceSpan.objects.filter(source=case.source).order_by('start')] == case.source.decoded_text.splitlines()
    old_facts = list(CanonicalFact.objects.filter(incident=case.incident).values())
    old_revision = case.incident.revision
    fake_completed_adapter(monkeypatch, case)
    response = call_extract(case)
    assert response.status_code == 200, response.content
    payload = response.json()
    assert (payload['status'], payload['accepted_count'], payload['rejected_count']) == ('SUCCESS', 2, 0)
    receipt = AIInferenceReceipt.objects.get(pk=payload['receipt_id'])
    assert (receipt.provider, receipt.model_id, receipt.status, receipt.latency_ms) == ('AMD', 'DeepSeek-V4-Flash', 'SUCCESS', 37)
    assert receipt.request_id == 'genuine-stub-header'
    assert (receipt.input_schema_version, receipt.output_schema_version) == ('v1', 'v1')
    assert receipt.timestamp.tzinfo is not None
    run = AIExtractionRun.objects.get(receipt=receipt)
    assert run.source_id == case.source.id
    claims = list(InventoryClaim.objects.filter(extraction_run=run).order_by('field'))
    assert {claim.field: claim.value for claim in claims} == {'on_hand': 80, 'reserved': 25}
    assert all(claim.status == 'OBSERVED_UNVERIFIED' and claim.source_id == case.source.id
               and claim.span.source_id == case.source.id and 'counted 80 boxes, 25 reserved' in claim.span.text
               for claim in claims)
    assert all(claim.subject_id == case.brook.stockbatch_set.get().id for claim in claims)
    assert list(CanonicalFact.objects.filter(incident=case.incident).values()) == old_facts
    case.incident.refresh_from_db()
    assert case.incident.revision == old_revision + 1
    stored = AIInferenceReceipt.objects.values().get(pk=receipt.id)
    assert not {'api_key', 'prompt', 'raw_content', 'raw_response', 'source_id'} & set(stored)
    assert 'counted 80 boxes' not in str(stored)
    assert 'counted 80 boxes' not in response.content.decode()


def test_changed_note_is_new_source_and_repeated_extraction_has_new_receipt(case, monkeypatch):
    from healthmesh.ai_gateway import service
    from healthmesh.sources.service import ingest_source

    fake_completed_adapter(monkeypatch, case)
    assert call_extract(case).status_code == 200
    first_receipt = AIInferenceReceipt.objects.get()
    assert call_extract(case).status_code == 200
    assert AIInferenceReceipt.objects.count() == 2
    assert AIInferenceReceipt.objects.exclude(pk=first_receipt.pk).exists()

    changed = ingest_source(case.incident.id, case.brook.id, 'new-note.txt',
                            b'SYNTHETIC Brook update: counted 70 boxes, 25 reserved.\n', case.actor)
    assert changed.id != case.source.id
    assert changed.sha256 != case.source.sha256

    class NewNoteAMD:
        def extract_claims(self, text, allowed_spans, schema_version):
            assert text == changed.decoded_text
            return ExtractionResult(status='SUCCESS',
                receipt=ReceiptMetadata(model_id='DeepSeek-V4-Flash', timestamp=datetime.now(timezone.utc),
                    latency_ms=10, status='SUCCESS', input_schema_version='v1', output_schema_version='v1'),
                raw_content=completion_content(allowed_spans, 70, 25))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: NewNoteAMD())
    response = call_extract(case, changed)
    assert response.status_code == 200
    assert response.json()['accepted_count'] == 2
    assert InventoryClaim.objects.filter(source=changed, extraction_run__isnull=False).count() == 2
    assert AIExtractionRun.objects.filter(source=changed).count() == 1


def test_timeout_and_unavailable_do_not_advance_state(case, monkeypatch):
    from healthmesh.ai_gateway import service

    before = (case.incident.revision, InventoryClaim.objects.count(),
              list(CanonicalFact.objects.filter(incident=case.incident).values()))

    class TimeoutAMD:
        def extract_claims(self, text, allowed_spans, schema_version):
            raise TimeoutError('do-not-surface-upstream-content')

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: TimeoutAMD())
    response = call_extract(case)
    assert response.status_code == 200
    assert response.json()['status'] == 'TIMEOUT'
    assert 'do-not-surface' not in response.content.decode()
    case.incident.refresh_from_db()
    assert before == (case.incident.revision, InventoryClaim.objects.count(),
                      list(CanonicalFact.objects.filter(incident=case.incident).values()))
    assert not AIInferenceReceipt.objects.exists()
    assert not AIExtractionRun.objects.exists()


def test_prompt_injection_and_altered_value_cannot_mutate_facts(case, monkeypatch):
    from healthmesh.ai_gateway import service

    before = list(CanonicalFact.objects.filter(incident=case.incident).values())

    class MaliciousAMD:
        def extract_claims(self, text, allowed_spans, schema_version):
            stock_line = next(span for span in allowed_spans if 'counted' in span.text)
            raw = json.dumps({'claims': [
                {'field': 'on_hand', 'value': 95, 'unit': 'box', 'source_span_id': str(stock_line.id)},
                {'field': 'approve_plan', 'value': 'yes', 'source_span_id': str(stock_line.id)},
            ]})
            return ExtractionResult(status='SUCCESS',
                receipt=ReceiptMetadata(model_id='DeepSeek-V4-Flash', timestamp=datetime.now(timezone.utc),
                    latency_ms=10, status='SUCCESS', input_schema_version='v1', output_schema_version='v1'),
                raw_content=raw)

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: MaliciousAMD())
    response = call_extract(case)
    assert response.status_code == 200
    assert response.json()['accepted_count'] == 0
    assert response.json()['rejected_count'] == 2
    assert not InventoryClaim.objects.filter(source=case.source).exists()
    assert list(CanonicalFact.objects.filter(incident=case.incident).values()) == before


class FakeResponse:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, limit): return self.body[:limit]


def test_token_factory_transport_uses_documented_request_and_preserves_raw_json(case, monkeypatch, settings):
    from healthmesh.ai_gateway import provider_event
    from healthmesh.ai_gateway.service import get_amd_adapter

    settings.AMD_API_KEY = 'fictional-test-key'
    settings.AMD_API_BASE_URL = 'https://developer.amd.com.cn/radeon/api/v1'
    settings.AMD_MODEL_ID = 'DeepSeek-V4-Flash'
    content = completion_content(SourceSpan.objects.filter(source=case.source))
    seen = []

    def open_stub(request, timeout):
        assert timeout > 0
        seen.append(request)
        assert request.full_url == 'https://developer.amd.com.cn/radeon/api/v1/chat/completions'
        assert request.get_header('Authorization') == 'Bearer fictional-test-key'
        assert request.get_header('Content-type') == 'application/json'
        body = json.loads(request.data)
        assert body['model'] == 'DeepSeek-V4-Flash'
        assert body['response_format'] == {'type': 'json_object'}
        issued = json.loads(body['messages'][1]['content'])
        assert issued['source_text'] == case.source.decoded_text
        assert str(SourceSpan.objects.filter(source=case.source).first().id) in [
            row['id'] for row in issued['issued_spans']]
        payload = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
        return FakeResponse(payload, {'X-Request-Id': 'amd-header-id'})

    monkeypatch.setattr(provider_event, '_open_request', open_stub)
    result = get_amd_adapter().extract_claims(case.source.decoded_text,
        tuple(SourceSpan.objects.filter(source=case.source)), 'v1')
    assert result.status == 'SUCCESS'
    assert result.raw_content == content
    assert result.receipt.provider == 'AMD'
    assert result.receipt.request_id == 'amd-header-id'
    assert result.receipt.model_id == 'DeepSeek-V4-Flash'
    assert len(seen) == 1
    assert 'raw_content' not in result.model_dump()


def test_retry_after_is_honored_and_permanent_errors_are_not_retried(case, monkeypatch, settings):
    from healthmesh.ai_gateway import provider_event
    from healthmesh.ai_gateway.service import get_amd_adapter

    settings.AMD_API_KEY = 'fictional-test-key'
    settings.AMD_API_BASE_URL = 'https://developer.amd.com.cn/radeon/api/v1'
    settings.AMD_MODEL_ID = 'DeepSeek-V4-Flash'
    calls, sleeps = [], []

    def rate_limited(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, 'rate limited', {'Retry-After': '1'}, BytesIO())
        return FakeResponse(b'{"choices":[{"message":{"content":"{\\"claims\\":[]}"}}]}')

    monkeypatch.setattr(provider_event, '_open_request', rate_limited)
    monkeypatch.setattr(provider_event.time, 'sleep', sleeps.append)
    result = get_amd_adapter().extract_claims(case.source.decoded_text,
        tuple(SourceSpan.objects.filter(source=case.source)), 'v1')
    assert result.status == 'SUCCESS'
    assert len(calls) == 2
    assert len(sleeps) == 1 and sleeps[0] >= 1

    def bad_request(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 400, 'bad parameter', {}, BytesIO())

    monkeypatch.setattr(provider_event, '_open_request', bad_request)
    calls.clear()
    result = get_amd_adapter().extract_claims(case.source.decoded_text,
        tuple(SourceSpan.objects.filter(source=case.source)), 'v1')
    assert result.status == 'UNAVAILABLE'
    assert result.receipt is None
    assert len(calls) == 1


def test_token_factory_redirect_does_not_forward_bearer_header(case, monkeypatch, settings):
    from healthmesh.ai_gateway import provider_event
    from healthmesh.ai_gateway.service import get_amd_adapter

    initial_requests, redirected_requests = [], []

    class RedirectServer(BaseHTTPRequestHandler):
        def do_POST(self):
            initial_requests.append(self.path)
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/steal')
            self.end_headers()

        def do_GET(self):
            redirected_requests.append((self.path, self.headers.get('Authorization')))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"{\\"claims\\":[]}"}}]}')

        def log_message(self, *_):
            pass

    server = HTTPServer(('127.0.0.1', 0), RedirectServer)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}/radeon/api/v1'
        monkeypatch.setattr(provider_event, 'TOKEN_FACTORY_BASE_URL', base)
        settings.AMD_API_BASE_URL = base
        settings.AMD_API_KEY = 'fictional-test-key'
        settings.AMD_MODEL_ID = 'DeepSeek-V4-Flash'
        result = get_amd_adapter().extract_claims(case.source.decoded_text,
            tuple(SourceSpan.objects.filter(source=case.source)), 'v1')
        assert result.status == 'UNAVAILABLE'
        assert result.receipt is None
        assert initial_requests == ['/radeon/api/v1/chat/completions']
        assert redirected_requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(('key', 'model'), [
    ('fictional-test-key\n', 'DeepSeek-V4-Flash'),
    ('fictional-test-key', 'X' * 201),
])
def test_invalid_secret_header_or_model_config_never_starts_transport(case, settings, key, model):
    from healthmesh.ai_gateway.service import get_amd_adapter

    settings.AMD_API_KEY = key
    settings.AMD_API_BASE_URL = 'https://developer.amd.com.cn/radeon/api/v1'
    settings.AMD_MODEL_ID = model
    assert get_amd_adapter().health()['status'] == 'UNAVAILABLE'
