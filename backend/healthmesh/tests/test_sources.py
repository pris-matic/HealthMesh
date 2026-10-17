import hashlib
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from healthmesh.models import CanonicalFact, Facility, InventoryClaim, SourceDocument, SourceSpan
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = pytest.mark.django_db
CSV = ('synthetic,product_code,batch_code,base_unit,reserve_accounting,on_hand,reserved,protected_reserve,observed_at\r\n'
       'true,SIM-MX-01,MX-01,box,separate,80,25,20,2026-10-17T11:00:00Z\r\n')


@pytest.fixture
def context(django_user_model):
    actor = django_user_model.objects.create_user('uploader', password='test-password')
    actor.groups.add(Group.objects.create(name='coordinator'))
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    facility = Facility.objects.get(incident=incident, name='Brook')
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    token = client.get('/api/v1/session').json()['csrf_token']
    return actor, incident, facility, client, token


def upload(context, content=CSV, filename='inventory.csv', **fields):
    _, incident, facility, client, token = context
    data = {'incident_id': str(incident.id), 'facility_id': str(facility.id),
            'file': SimpleUploadedFile(filename, content.encode('utf-8') if isinstance(content, str) else content)}
    data.update(fields)
    return client.post('/api/v1/sources', data, HTTP_X_CSRFTOKEN=token)


def counts(incident):
    incident.refresh_from_db()
    return (SourceDocument.objects.count(), SourceSpan.objects.count(), InventoryClaim.objects.count(), incident.revision)


def test_csv_round_trip_and_duplicate_hash(context):
    _, incident, facility, _, _ = context
    before = counts(incident)
    canonical = list(CanonicalFact.objects.values())
    response = upload(context)
    assert response.status_code == 201, response.content
    payload = response.json()
    source = SourceDocument.objects.get(pk=payload['id'])
    assert payload['duplicate'] is False
    assert payload['sha256'] == hashlib.sha256(CSV.encode()).hexdigest()
    assert payload['kind'] == 'CSV'
    assert payload['observed_at'] == '2026-10-17T11:00:00+00:00'
    assert source.facility_id == facility.id
    assert bytes(source.original_blob) == CSV.encode()
    assert source.decoded_text == CSV
    claims = list(InventoryClaim.objects.filter(source=source))
    assert {c.field: c.value for c in claims} == {'on_hand': 80, 'reserved': 25, 'protected_reserve': 20, 'reserve_accounting': 'separate'}
    for claim in claims:
        assert claim.status == 'OBSERVED_UNVERIFIED'
        assert claim.subject_id == facility.stockbatch_set.get().id
        assert claim.span.text == CSV.splitlines()[1]
        assert claim.observed_at == source.observed_at
    assert list(CanonicalFact.objects.values()) == canonical
    after = counts(incident)
    assert after == (before[0] + 1, before[1] + 1, before[2] + 4, before[3] + 1)
    repeated = upload(context, filename='renamed.csv')
    assert repeated.status_code == 200
    assert repeated.json()['id'] == payload['id']
    assert repeated.json()['duplicate'] is True
    assert counts(incident) == after
    # Facility is part of the deduplication scope.
    other = Facility.objects.get(incident=incident, name='Cedar')
    scoped = upload(context, facility_id=str(other.id))
    assert scoped.status_code == 201
    assert scoped.json()['id'] != payload['id']


@pytest.mark.parametrize('content,filename', [
    (CSV.replace(',box,', ',tablet,'), 'x.csv'),
    (CSV.replace(',80,', ',-1,'), 'x.csv'),
    (CSV.replace(',80,', ',1.5,'), 'x.csv'),
    (CSV.replace('separate', 'included'), 'x.csv'),
    (CSV.replace('on_hand', 'patient_name'), 'x.csv'),
    (CSV.replace('on_hand', 'unknown_field'), 'x.csv'),
    (CSV.replace('true,', 'false,'), 'x.csv'),
    (CSV.replace('SIM-MX-01', 'REAL-MED'), 'x.csv'),
    (CSV.replace('MX-01,box', 'unknown,box'), 'x.csv'),
    (CSV.replace('11:00:00Z', '11:00:00'), 'x.csv'),
    (CSV.replace('2026-10-17', '2026-10-18'), 'x.csv'),
    (CSV + 'true,SIM-MX-01,MX-01,box,separate,-2,25,20,2026-10-17T11:00:00Z', 'x.csv'),
    (CSV.splitlines()[0], 'x.csv'),
    ('SYNTHETIC report\nPatient: Jane Example\nMRN: 123456', 'x.txt'),
    ('SYNTHETIC report\nDiagnosis: diabetes', 'x.txt'),
    ('Inventory report without synthetic marker', 'x.txt'),
    (b'\xff\xfe', 'x.txt'),
    ('SYNTHETIC\x00 report', 'x.txt'),
    ('', 'x.txt'),
    (CSV, 'x.pdf'),
])
def test_rejects_wrong_unit_negative_quantity_pii_header_and_oversize(context, content, filename):
    before = counts(context[1])
    response = upload(context, content, filename)
    assert 400 <= response.status_code < 500
    assert set(response.json()) == {'code', 'message', 'field_errors', 'request_id'}
    assert response.json()['field_errors']
    assert counts(context[1]) == before


def test_oversize_is_json_and_has_no_side_effects(context, settings):
    settings.SOURCE_MAX_BYTES = 128
    before = counts(context[1])
    response = upload(context, 'SYNTHETIC ' + 'x' * 129, 'x.txt')
    assert response.status_code == 413
    assert set(response.json()) == {'code', 'message', 'field_errors', 'request_id'}
    assert counts(context[1]) == before


def test_text_spans_match_sanitized_offsets(context):
    text = 'SYNTHETIC supply note\r\nCafe\u0301 😀: 80 boxes; email demo@example.test\r\n25 reserved. Phone +63 917 123 4567\r\n'
    response = upload(context, text, 'note.txt')
    assert response.status_code == 201, response.content
    source = SourceDocument.objects.get(pk=response.json()['id'])
    assert bytes(source.original_blob) == text.encode()
    amd_text = source.decoded_text
    assert len(amd_text) == len(text)
    assert 'demo@example.test' not in amd_text
    assert '+63 917 123 4567' not in amd_text
    assert 'Cafe\u0301 😀: 80 boxes' in amd_text
    spans = list(SourceSpan.objects.filter(source=source).order_by('start'))
    assert len(spans) == 3
    for span, quoted_text in zip(spans, amd_text.splitlines()):
        assert amd_text[span.start:span.end] == quoted_text == span.text
    assert not InventoryClaim.objects.filter(source=source).exists()
    assert 'original_blob' not in response.json()
    assert 'decoded_text' not in response.json()


def test_scope_auth_csrf_errors_and_facility_choices(context, django_user_model):
    actor, incident, facility, client, token = context
    choices = client.post('/api/v1/scenarios', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=token)
    assert choices.status_code == 201
    assert {row['name'] for row in choices.json()['facilities']} == {'Aster', 'Brook', 'Cedar'}
    before = counts(incident)
    assert upload(context, incident_id='invalid').status_code == 400
    assert upload(context, facility_id=str(uuid4())).status_code == 404
    other = create_incident(load_scenario('storm-supply-01'), actor)
    foreign = Facility.objects.filter(incident=other).first()
    assert upload(context, facility_id=str(foreign.id)).status_code == 404
    assert client.post('/api/v1/sources', {}).status_code == 403
    assert client.get('/api/v1/sources').status_code == 405
    assert upload(context, extra='unexpected').status_code == 400
    actor.groups.clear()
    actor.groups.add(Group.objects.create(name='reviewer'))
    assert upload(context).status_code == 403
    client.logout()
    anonymous_token = client.get('/api/v1/session').json()['csrf_token']
    response = client.post('/api/v1/sources', {}, HTTP_X_CSRFTOKEN=anonymous_token)
    assert response.status_code == 401
    incident.refresh_from_db()
    assert incident.revision == before[3]


def test_ingest_service_duplicate_does_not_advance_revision(context):
    from healthmesh.sources.service import ingest_source
    actor, incident, facility, _, _ = context
    source = ingest_source(incident.id, facility.id, 'x.csv', CSV.encode(), actor)
    after = counts(incident)
    duplicate = ingest_source(incident.id, facility.id, 'other.csv', CSV.encode(), actor)
    assert duplicate.id == source.id
    assert counts(incident) == after


def test_upload_preserves_frozen_plan(context):
    _, incident, _, client, token = context
    plan = client.post(f'/api/v1/incidents/{incident.id}/plan', HTTP_X_CSRFTOKEN=token).json()
    assert upload(context).status_code == 201
    frozen = client.get(f"/api/v1/incidents/{incident.id}/plan-runs/{plan['id']}").json()
    assert frozen == plan



def test_multipart_field_limits_return_json_before_csrf(context, settings):
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 2
    response = upload(context, unexpected='third field')
    assert response.status_code == 413
    assert response['Content-Type'] == 'application/json'
    assert set(response.json()) == {'code', 'message', 'field_errors', 'request_id'}


def test_excessive_lines_fail_without_creating_evidence(context):
    before = counts(context[1])
    response = upload(context, 'SYNTHETIC\n' + '80 boxes\n' * 2000, 'many.txt')
    assert response.status_code == 400
    assert counts(context[1]) == before


@pytest.mark.parametrize('content', [
    'SYNTHETIC\npatient_name: Example',
    'SYNTHETIC\nname: Example Person',
    'SYNTHETIC\naddress: 123 Example Street',
    'SYNTHETIC\nAllergies: penicillin',
    'SYNTHETIC\nJohn has diabetes and takes insulin daily.',
])
def test_rejects_apparent_personal_or_clinical_note(context, content):
    before = counts(context[1])
    response = upload(context, content, 'unsafe.txt')
    assert response.status_code == 400
    assert counts(context[1]) == before


def test_csv_rejects_unicode_separator_in_timestamp(context):
    before = counts(context[1])
    response = upload(context, CSV.replace('T11:', '\u202811:'))
    assert response.status_code == 400
    assert counts(context[1]) == before
