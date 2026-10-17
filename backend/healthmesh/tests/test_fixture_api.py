import json
from uuid import UUID

import pytest
from django.contrib.auth.models import Group
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(django_user_model):
    user = django_user_model.objects.create_user('demo-coordinator', password='test-password')
    user.groups.add(Group.objects.create(name='coordinator'))
    client = Client(enforce_csrf_checks=True)
    return client


def login(client):
    response = client.get('/api/v1/session')
    assert response.status_code == 200
    token = response.json()['csrf_token']
    response = client.post('/api/v1/session', json.dumps({'username': 'demo-coordinator', 'password': 'test-password'}), content_type='application/json', HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200
    return response.json()['csrf_token']


def test_fixture_to_frozen_plan(client):
    token = login(client)
    created = client.post('/api/v1/scenarios', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=token)
    assert created.status_code == 201
    scenario = created.json()
    UUID(scenario['id'])
    assert scenario['seed'] == 'storm-supply-01'
    url = f"/api/v1/incidents/{scenario['id']}/plan"
    response = client.post(url, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 201
    plan = response.json()
    assert plan['revision'] == 1
    assert plan['status'] == 'PROVISIONAL'
    assert len(plan['branches']) == 2
    assert plan['mandatory_unverified_facts']
    assert all(branch['conditions'] for branch in plan['branches'])
    assert all(check['reason'] for branch in plan['branches'] for check in branch['checks'])
    assert all(sum(a['quantity'] for a in b['allocations']) == 40 for b in plan['branches'])
    from healthmesh.models import PlanRun, PlanProposal
    run = PlanRun.objects.get(pk=plan['id'])
    assert run.input_snapshot['revision'] == 1
    assert PlanProposal.objects.filter(run=run).count() == 2
    assert {str(p.id) for p in run.proposals.all()} == {b['id'] for b in plan['branches']}
    from healthmesh.db_models.core import incident_mutation
    with incident_mutation(scenario['id']):
        pass
    fetched = client.get(f"/api/v1/incidents/{scenario['id']}/plan-runs/{plan['id']}")
    assert fetched.json() == plan


def test_auth_csrf_and_errors(client, django_user_model):
    response = client.post('/api/v1/scenarios')
    assert response.status_code in (401, 403)
    assert set(response.json()) == {'code', 'message', 'field_errors', 'request_id'}
    anonymous_token = client.get('/api/v1/session').json()['csrf_token']
    assert client.post('/api/v1/scenarios', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=anonymous_token).status_code == 401
    token = login(client)
    assert client.post('/api/v1/scenarios').status_code == 403
    bad = client.post('/api/v1/scenarios', '{', content_type='application/json', HTTP_X_CSRFTOKEN=token)
    assert bad.status_code == 400
    assert set(bad.json()) == {'code', 'message', 'field_errors', 'request_id'}
    assert client.get('/api/v1/incidents/00000000-0000-0000-0000-000000000001/plan-runs/00000000-0000-0000-0000-000000000002').status_code == 404
    user = django_user_model.objects.get(username='demo-coordinator')
    user.groups.clear()
    assert client.post('/api/v1/scenarios', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code == 403


def test_logout_and_bad_login(client):
    token = client.get('/api/v1/session').json()['csrf_token']
    assert client.post('/api/v1/session', json.dumps({'username': 'demo-coordinator', 'password': 'wrong'}), content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code == 401
    token = login(client)
    assert client.delete('/api/v1/session', HTTP_X_CSRFTOKEN=token).status_code == 200
    assert client.get('/api/v1/session').json()['user'] is None


def test_seed_users_require_environment_passwords(monkeypatch, django_user_model):
    from django.core.management import call_command, CommandError
    monkeypatch.delenv('DEMO_COORDINATOR_PASSWORD', raising=False)
    monkeypatch.delenv('DEMO_REVIEWER_PASSWORD', raising=False)
    with pytest.raises(CommandError):
        call_command('seed_demo_users')
    assert not django_user_model.objects.exists()
    monkeypatch.setenv('DEMO_COORDINATOR_PASSWORD', 'synthetic-coordinator-password')
    monkeypatch.setenv('DEMO_REVIEWER_PASSWORD', 'synthetic-reviewer-password')
    call_command('seed_demo_users')
    call_command('seed_demo_users')
    assert django_user_model.objects.count() == 2
    reviewer = django_user_model.objects.get(username='demo-reviewer')
    assert reviewer.check_password('synthetic-reviewer-password')
    assert list(reviewer.groups.values_list('name', flat=True)) == ['reviewer']


def test_plan_records_cannot_be_rewritten(client):
    from django.core.exceptions import ValidationError
    from healthmesh.models import PlanRun
    token = login(client)
    incident = client.post('/api/v1/scenarios', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=token).json()
    result = client.post(f"/api/v1/incidents/{incident['id']}/plan", HTTP_X_CSRFTOKEN=token).json()
    run = PlanRun.objects.get(pk=result['id'])
    run.result = {}
    with pytest.raises(ValidationError):
        run.save()
    with pytest.raises(ValidationError):
        PlanRun.objects.filter(pk=run.id).update(result={})
    proposal = run.proposals.first()
    proposal.branch = {}
    with pytest.raises(ValidationError):
        proposal.save()
    with pytest.raises(ValidationError):
        run.proposals.all().delete()
