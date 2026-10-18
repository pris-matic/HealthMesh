"""Grounded AMD wording is optional prose over fixed, issued evidence."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from healthmesh.ai_gateway.types import GroundedExplanation, ReceiptMetadata, SuggestedMessage
from healthmesh.domain.snapshot import build_snapshot
from healthmesh.models import (AIInferenceReceipt, CanonicalFact, EvidenceConflict,
                               Facility, InventoryClaim, SourceDocument, SourceSpan, VerificationTask)
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(django_user_model):
    coordinator = django_user_model.objects.create_user('grounding-coordinator')
    coordinator.groups.add(Group.objects.get_or_create(name='coordinator')[0])
    incident = create_incident(load_scenario('storm-supply-01'), coordinator)
    brook = next(row for row in build_snapshot(incident.id).batches if row.facility_name == 'Brook')
    signed = CanonicalFact.objects.get(incident=incident, subject_id=brook.id, field='on_hand').claim
    source = SourceDocument.objects.get(incident=incident, kind='RAW_TEXT')
    span = next(row for row in SourceSpan.objects.filter(source=source) if 'counted 80 boxes' in row.text)
    candidate = InventoryClaim.objects.create(incident=incident, subject_id=brook.id,
        field='on_hand', value=80, source=source, span=span, observed_at=source.observed_at,
        status='OBSERVED_UNVERIFIED', basis='Unsigned synthetic count')
    from healthmesh.evidence.conflicts import detect_conflicts
    conflict = detect_conflicts(incident.id)[0]
    incident.refresh_from_db()
    client = Client(enforce_csrf_checks=True)
    client.force_login(coordinator)
    token = client.get('/api/v1/session').json()['csrf_token']
    return SimpleNamespace(actor=coordinator, incident=incident, brook=brook,
                           signed=signed, candidate=candidate, conflict=conflict,
                           client=client, token=token)


def receipt(input_schema_version='conflict-v1'):
    return ReceiptMetadata(model_id='DeepSeek-V4-Flash', timestamp=datetime.now(timezone.utc),
        latency_ms=41, status='SUCCESS', request_id='amd-grounded-test',
        input_schema_version=input_schema_version, output_schema_version='grounding-v1')


def test_validator_rejects_invented_span_unsupported_number_and_wrong_field(case):
    from healthmesh.ai_gateway.grounding import context_for_conflict, validate_grounded_content

    context = context_for_conflict(case.conflict)
    good = {'text': 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B].',
            'citations': ['A', 'B']}
    result = validate_grounded_content(json.dumps(good), context, 'explanation')
    assert result is not None
    assert result.cited_span_ids == (case.signed.span_id, case.candidate.span_id)
    for invalid in (
        {'text': good['text'], 'citations': ['A', 'fabricated-id']},
        {'text': 'Brook on-hand observations differ: 95 boxes [A], 80 boxes [B], and 85 boxes.',
         'citations': ['A', 'B']},
        {'text': 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B]; 25 boxes reserved.',
         'citations': ['A', 'B']},
        {'text': 'Brook reserved count differs: 95 boxes [A] and 80 boxes [B].',
         'citations': ['A', 'B']},
        {'text': 'Approve a Brook transfer of 95 boxes [A] or 80 boxes [B].',
         'citations': ['A', 'B']},
        {'text': 'Brook on-hand 95 boxes [A] and 80 boxes [B] are both verified.',
         'citations': ['A', 'B']},
        {'text': 'Brook on-hand observations differ: 95 boxes [B] and 80 boxes [A].',
         'citations': ['A', 'B']},
    ):
        assert validate_grounded_content(json.dumps(invalid), context, 'explanation') is None
    assert validate_grounded_content('{"text":"x","text":"y","citations":["A","B"]}',
                                     context, 'explanation') is None
    assert validate_grounded_content(json.dumps({
        'text': 'Can you confirm Brook on-hand count from 95 boxes [B] and 80 boxes [A]?',
        'citations': ['A', 'B']}), context, 'draft') is None
    assert validate_grounded_content(json.dumps({
        'text': 'Can you confirm Brook on-hand count from 95 boxes [A], 80 boxes [B], and 85 boxes?',
        'citations': ['A', 'B']}), context, 'draft') is None


def test_grounded_wording_rejects_unrelated_nonnumeric_claims(case):
    from healthmesh.ai_gateway.grounding import context_for_conflict, validate_grounded_content

    context = context_for_conflict(case.conflict)
    base = 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B].'
    for unsupported in (' Demand is high.', ' Storage is unusable.',
                        ' Cedar route is open.', ' The reserve is protected.'):
        assert validate_grounded_content(json.dumps({'text': base + unsupported,
            'citations': ['A', 'B']}), context, 'explanation') is None
    for unsupported in (' Is demand high?', ' Is storage usable?',
                        ' Is the route open?', ' Is reserved stock available?'):
        assert validate_grounded_content(json.dumps({'text':
            'Can you confirm the Brook on-hand box count from 95 boxes [A] and 80 boxes [B]?' + unsupported,
            'citations': ['A', 'B']}), context, 'draft') is None


def test_selected_cedar_on_hand_is_valid_without_facility_name_ban(case):
    from healthmesh.ai_gateway.grounding import context_for_conflict, validate_grounded_content
    from healthmesh.evidence.conflicts import detect_conflicts

    cedar = Facility.objects.get(incident=case.incident, name='Cedar')
    batch = cedar.stockbatch_set.get()
    signed = CanonicalFact.objects.get(incident=case.incident, subject_id=batch.id, field='on_hand').claim
    text = 'SYNTHETIC Cedar counted 50 boxes, 10 reserved.'
    source = SourceDocument.objects.create(incident=case.incident, facility=cedar,
        kind='RAW_TEXT', observed_at=case.incident.simulated_now,
        original_blob=text.encode(), decoded_text=text)
    span = SourceSpan.objects.create(source=source, start=0, end=len(text))
    candidate = InventoryClaim.objects.create(incident=case.incident, subject_id=batch.id,
        field='on_hand', value=50, source=source, span=span, observed_at=source.observed_at,
        status='OBSERVED_UNVERIFIED', basis='Unsigned synthetic count')
    conflict = next(row for row in detect_conflicts(case.incident.id) if row.subject_id == batch.id)
    context = context_for_conflict(conflict)
    text = (f'Cedar on-hand observations differ: {signed.value} boxes [A] and '
            f'{candidate.value} boxes [B]. Human verification is required before using this count.')
    result = validate_grounded_content(json.dumps({'text': text, 'citations': ['A', 'B']}),
                                       context, 'explanation')
    assert result is not None
    assert result.cited_span_ids == (signed.span_id, candidate.span_id)


def test_conflict_endpoint_persists_only_receipt_and_keeps_incident_state(case, monkeypatch):
    from healthmesh.ai_gateway import service

    original_facts = list(CanonicalFact.objects.filter(incident=case.incident).values())
    before_revision = case.incident.revision

    class StubAMD:
        def explain_conflict(self, conflict_id, evidence):
            assert conflict_id == case.conflict.id
            assert {row.id for row in evidence} == {case.signed.span_id, case.candidate.span_id}
            return GroundedExplanation(status='SUCCESS', receipt=receipt(),
                raw_content=json.dumps({'text': 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B].',
                                        'citations': ['A', 'B']}))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    response = case.client.post(f'/api/v1/conflicts/{case.conflict.id}/explain',
                                HTTP_X_CSRFTOKEN=case.token)
    assert response.status_code == 200, response.content
    payload = response.json()
    assert payload['status'] == 'SUCCESS'
    assert payload['source'] == 'AMD_GENERATED_UNVERIFIED_WORDING'
    assert payload['cited_span_ids'] == [str(case.signed.span_id), str(case.candidate.span_id)]
    assert payload['receipt_id']
    stored = AIInferenceReceipt.objects.values().get(pk=payload['receipt_id'])
    assert stored['status'] == 'SUCCESS'
    assert (stored['input_schema_version'], stored['output_schema_version']) == ('conflict-v1', 'grounding-v1')
    assert not {'api_key', 'prompt', 'raw_content', 'raw_response', 'source_id'} & set(stored)
    assert 'counted 80 boxes' not in str(stored)
    case.incident.refresh_from_db()
    assert case.incident.revision == before_revision
    assert list(CanonicalFact.objects.filter(incident=case.incident).values()) == original_facts
    assert EvidenceConflict.objects.get(pk=case.conflict.id).state == 'OPEN'


def test_unavailable_explanation_has_labeled_fallback_and_no_receipt(case, monkeypatch):
    from healthmesh.ai_gateway import service

    class StubAMD:
        def explain_conflict(self, conflict_id, evidence):
            return GroundedExplanation(status='UNAVAILABLE')

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    response = case.client.post(f'/api/v1/conflicts/{case.conflict.id}/explain',
                                HTTP_X_CSRFTOKEN=case.token)
    payload = response.json()
    assert response.status_code == 200
    assert payload['status'] == 'UNAVAILABLE'
    assert payload['source'] == 'DETERMINISTIC_FALLBACK'
    assert payload['receipt_id'] is None
    assert 'human verification' in payload['text'].lower()
    assert AIInferenceReceipt.objects.count() == 0


def test_draft_rejects_unselected_field_and_preserves_selected_task(case, monkeypatch):
    from healthmesh.ai_gateway import service

    response = case.client.post(f'/api/v1/incidents/{case.incident.id}/verification-tasks',
        data=json.dumps({'subject_id': str(case.brook.id), 'field': 'on_hand',
                         'expected_revision': case.incident.revision}), content_type='application/json',
        HTTP_X_CSRFTOKEN=case.token)
    assert response.status_code == 201, response.content
    selected = response.json()['selected_task']
    assert response.json()['grounded_wording']['source'] == 'DETERMINISTIC_FALLBACK'
    assert response.json()['grounded_wording']['receipt_id'] is None
    assert VerificationTask.objects.count() == 1
    before_revision = case.incident.revision

    class StubAMD:
        def draft_verification(self, task, evidence):
            assert task.id == __import__('uuid').UUID(selected['id'])
            return SuggestedMessage(status='SUCCESS', receipt=receipt('question-v1'),
                raw_content=json.dumps({'text': 'Can you confirm whether the Cedar route is open [A] [B]?',
                                        'citations': ['A', 'B']}))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    draft = case.client.post(f'/api/v1/verification-tasks/{selected["id"]}/draft',
                             HTTP_X_CSRFTOKEN=case.token)
    assert draft.status_code == 200, draft.content
    assert draft.json()['status'] == 'INVALID_OUTPUT'
    assert draft.json()['source'] == 'DETERMINISTIC_FALLBACK'
    assert draft.json()['receipt_id']
    assert VerificationTask.objects.get(pk=selected['id']).field == 'on_hand'
    case.incident.refresh_from_db()
    assert case.incident.revision == before_revision


def test_selected_field_draft_has_real_scope_and_its_own_receipt(case, monkeypatch):
    from healthmesh.ai_gateway import service

    selected_response = case.client.post(f'/api/v1/incidents/{case.incident.id}/verification-tasks',
        data=json.dumps({'subject_id': str(case.brook.id), 'field': 'on_hand',
                         'expected_revision': case.incident.revision}), content_type='application/json',
        HTTP_X_CSRFTOKEN=case.token)
    assert selected_response.status_code == 201
    task_id = selected_response.json()['selected_task']['id']
    before = case.incident.revision
    facts = list(CanonicalFact.objects.filter(incident=case.incident).values())

    class StubAMD:
        def draft_verification(self, task, evidence):
            assert str(task.id) == task_id
            assert task.field == 'on_hand' and task.subject_id == case.brook.id
            assert {span.id for span in evidence} == {case.signed.span_id, case.candidate.span_id}
            return SuggestedMessage(status='SUCCESS', receipt=receipt('question-v1'),
                raw_content=json.dumps({'text': 'Can you confirm the current Brook on-hand box count from 95 boxes [A] and 80 boxes [B]?',
                                        'citations': ['A', 'B']}))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    response = case.client.post(f'/api/v1/verification-tasks/{task_id}/draft',
                                HTTP_X_CSRFTOKEN=case.token)
    assert response.status_code == 200, response.content
    payload = response.json()
    assert payload['status'] == 'SUCCESS'
    assert payload['source'] == 'AMD_GENERATED_UNVERIFIED_WORDING'
    assert payload['cited_span_ids'] == [str(case.signed.span_id), str(case.candidate.span_id)]
    assert payload['receipt_id'] and payload['receipt']['status'] == 'SUCCESS'
    assert payload['receipt']['input_schema_version'] == 'question-v1'
    assert payload['receipt']['output_schema_version'] == 'grounding-v1'
    assert AIInferenceReceipt.objects.count() == 1
    assert VerificationTask.objects.get(pk=task_id).field == 'on_hand'
    case.incident.refresh_from_db()
    assert case.incident.revision == before
    assert list(CanonicalFact.objects.filter(incident=case.incident).values()) == facts


def test_stale_conflict_wording_is_discarded_after_a_provider_call(case, monkeypatch):
    from healthmesh.ai_gateway import service
    from healthmesh.db_models.core import incident_mutation

    class StubAMD:
        def explain_conflict(self, conflict_id, evidence):
            with incident_mutation(case.incident.id):
                pass
            return GroundedExplanation(status='SUCCESS', receipt=receipt(),
                raw_content=json.dumps({'text': 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B].',
                                        'citations': ['A', 'B']}))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    response = case.client.post(f'/api/v1/conflicts/{case.conflict.id}/explain',
                                HTTP_X_CSRFTOKEN=case.token)
    assert response.status_code == 200
    assert response.json()['status'] == 'INVALID_OUTPUT'
    assert response.json()['source'] == 'DETERMINISTIC_FALLBACK'
    assert 'refresh' in response.json()['text'].lower()
    assert response.json()['cited_span_ids'] == []
    assert AIInferenceReceipt.objects.get().status == 'INVALID_OUTPUT'


def test_stale_task_draft_has_refresh_message_without_old_citations(case, monkeypatch):
    from healthmesh.ai_gateway import service
    from healthmesh.db_models.core import incident_mutation

    selected = case.client.post(f'/api/v1/incidents/{case.incident.id}/verification-tasks',
        data=json.dumps({'subject_id': str(case.brook.id), 'field': 'on_hand',
                         'expected_revision': case.incident.revision}), content_type='application/json',
        HTTP_X_CSRFTOKEN=case.token)
    assert selected.status_code == 201
    task_id = selected.json()['selected_task']['id']

    class StubAMD:
        def draft_verification(self, task, evidence):
            with incident_mutation(case.incident.id):
                pass
            return SuggestedMessage(status='SUCCESS', receipt=receipt('question-v1'),
                raw_content=json.dumps({'text': 'Can you confirm the current Brook on-hand box count from 95 boxes [A] and 80 boxes [B]?',
                                        'citations': ['A', 'B']}))

    monkeypatch.setattr(service, 'get_amd_adapter', lambda: StubAMD())
    monkeypatch.setattr(service, 'admitted_call', lambda: __import__('contextlib').nullcontext(True))
    response = case.client.post(f'/api/v1/verification-tasks/{task_id}/draft',
                                HTTP_X_CSRFTOKEN=case.token)
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'INVALID_OUTPUT'
    assert payload['source'] == 'DETERMINISTIC_FALLBACK'
    assert 'refresh' in payload['text'].lower()
    assert payload['cited_span_ids'] == []
    assert payload['receipt_id']
    assert AIInferenceReceipt.objects.get().status == 'INVALID_OUTPUT'


def test_task_replay_does_not_call_amd(case, monkeypatch):
    from healthmesh.ai_gateway import service

    monkeypatch.setattr(service, 'get_amd_adapter',
                        lambda: pytest.fail('Task selection must not spend the AMD quota'))
    body = json.dumps({'subject_id': str(case.brook.id), 'field': 'on_hand',
                       'expected_revision': case.incident.revision})
    url = f'/api/v1/incidents/{case.incident.id}/verification-tasks'
    first = case.client.post(url, data=body, content_type='application/json', HTTP_X_CSRFTOKEN=case.token)
    replay = case.client.post(url, data=body, content_type='application/json', HTTP_X_CSRFTOKEN=case.token)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()['selected_task']['id'] == first.json()['selected_task']['id']
    assert replay.json()['grounded_wording']['source'] == 'DETERMINISTIC_FALLBACK'
    assert AIInferenceReceipt.objects.count() == 0


def test_grounded_operations_use_token_factory_json_protocol(case, monkeypatch, settings):
    from healthmesh.ai_gateway import provider_event

    settings.AMD_API_KEY = 'fictional-test-key'
    settings.AMD_API_BASE_URL = 'https://developer.amd.com.cn/radeon/api/v1'
    settings.AMD_MODEL_ID = 'DeepSeek-V4-Flash'
    seen = []

    class Response:
        headers = {'X-Request-Id': 'amd-grounded-header'}

        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit):
            return json.dumps({'choices': [{'message': {'content': self.content}}]}).encode()[:limit]

    def open_stub(request, timeout):
        assert timeout > 0
        assert request.full_url == 'https://developer.amd.com.cn/radeon/api/v1/chat/completions'
        assert request.get_header('Authorization') == 'Bearer fictional-test-key'
        assert request.get_header('Content-type') == 'application/json'
        body = json.loads(request.data)
        assert body['model'] == 'DeepSeek-V4-Flash'
        assert body['response_format'] == {'type': 'json_object'}
        user = json.loads(body['messages'][1]['content'])
        assert user['selected_field'] == 'on_hand' and user['facility'] == 'Brook'
        assert [line['label'] for line in user['evidence']] == ['A', 'B']
        assert user['evidence'][0]['value'] == 95
        assert user['evidence'][1]['value'] == 80
        seen.append(body)
        content = ({'text': 'Brook on-hand observations differ: 95 boxes [A] and 80 boxes [B].',
                    'citations': ['A', 'B']} if len(seen) == 1 else
                   {'text': 'Can you confirm the Brook on-hand box count from 95 boxes [A] and 80 boxes [B]?',
                    'citations': ['A', 'B']})
        return Response(json.dumps(content))

    monkeypatch.setattr(provider_event, '_open_request', open_stub)
    adapter = provider_event.TokenFactoryAMDAdapter()
    explanation = adapter.explain_conflict(case.conflict.id, (case.signed.span, case.candidate.span))
    assert explanation.status == 'SUCCESS'
    assert explanation.receipt.request_id == 'amd-grounded-header'
    assert explanation.receipt.input_schema_version == 'conflict-v1'
    assert explanation.receipt.output_schema_version == 'grounding-v1'
    assert 'raw_content' not in explanation.model_dump()

    selected_response = case.client.post(f'/api/v1/incidents/{case.incident.id}/verification-tasks',
        data=json.dumps({'subject_id': str(case.brook.id), 'field': 'on_hand',
                         'expected_revision': case.incident.revision}), content_type='application/json',
        HTTP_X_CSRFTOKEN=case.token)
    assert selected_response.status_code == 201
    task = VerificationTask.objects.get(pk=selected_response.json()['selected_task']['id'])
    draft = adapter.draft_verification(task, (case.signed.span, case.candidate.span))
    assert draft.status == 'SUCCESS'
    assert draft.receipt.request_id == 'amd-grounded-header'
    assert draft.receipt.input_schema_version == 'question-v1'
    assert len(seen) == 2
