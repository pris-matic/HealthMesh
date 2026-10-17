from pathlib import Path
import json
from uuid import UUID

import pytest

from healthmesh.scenarios.fixture import ScenarioValidationError, load_scenario

ROOT = Path(__file__).resolve().parents[3]


def test_storm_fixture_is_complete_and_synthetic():
    seed = load_scenario('storm-supply-01')
    assert seed.scenario_id == 'storm-supply-01'
    assert seed.fixture_revision == 1
    assert seed.synthetic is True
    facts = seed.public_facts
    assert facts.as_of.isoformat() == '2026-10-17T12:00:00+00:00'
    assert facts.request.need_by.isoformat() == '2026-10-18T12:00:00+00:00'
    assert facts.request.quantity == 40
    assert facts.request.is_net_transfer_request is True
    assert facts.product.code == 'SIM-MX-01'
    assert facts.product.base_unit == 'box'
    facilities = {f.name: f for f in facts.facilities}
    assert set(facilities) == {'Aster', 'Brook', 'Cedar'}
    aster, brook, cedar = (facilities[name] for name in ('Aster', 'Brook', 'Cedar'))
    assert facts.request.facility_id == aster.id
    assert aster.stock.on_hand == 10
    assert brook.stock.on_hand == 95
    assert brook.stock.count_observed_at.isoformat() == '2026-10-15T09:00:00+00:00'
    assert brook.stock.reserved == 25
    assert brook.stock.protected_reserve == 20
    assert cedar.stock.on_hand == 55
    assert cedar.stock.reserved == 0
    assert cedar.stock.protected_reserve == 10
    for facility in facts.facilities:
        assert isinstance(facility.id, UUID)
        assert len(facility.daily_demand) == 14
        assert [row.units for row in facility.daily_demand] == [5 if facility.name == 'Aster' else 2] * 14
        assert str(facility.daily_demand[0].date) == '2026-10-03'
        assert str(facility.daily_demand[-1].date) == '2026-10-16'
        assert str(facility.stock.expires_on) == '2026-12-31'
        assert facility.stock.storage_usable is True
        assert facility.stock.observed_at.isoformat() == '2026-10-17T10:00:00+00:00'
        assert facility.stock.reserve_accounting == 'separate'
    routes = {route.origin_id: route for route in facts.routes}
    assert routes[brook.id].status == 'OPEN'
    assert routes[brook.id].verified_open_eta_hours == 2
    assert routes[cedar.id].status == 'UNKNOWN'
    assert routes[cedar.id].verified_open_eta_hours == 3
    assert all(route.destination_id == aster.id for route in routes.values())
    note = next(source for source in seed.source_references if source.kind == 'RAW_TEXT')
    assert '80 boxes' in note.text
    assert '25 reserved' in note.text
    assert note.observed_at.isoformat() == '2026-10-17T11:00:00+00:00'
    assert note.verified is False
    assert seed.policy.freshness_hours == 24
    assert seed.policy.verification_budget.staff_minutes == 10
    assert seed.policy.verification_budget.questions == 1
    assert seed.policy.unit_aliases == {'boxes': 'box'}
    assert seed.policy.coverage_alert_days == 3
    assert seed.policy.protected_reserve_counts_for_local_coverage is True
    assert [(q.staff_minutes, q.response_delay_minutes) for q in seed.policy.verification_tasks] == [(3, 10), (5, 20)]
    assert seed.policy.public_possibilities.brook_on_hand == (70, 90)
    assert seed.policy.public_possibilities.cedar_route == ('CLOSED', 'OPEN')
    assert seed.policy.public_possibilities.weights is None
    data = json.loads((ROOT / 'fixtures/scenarios/storm-supply-01.json').read_text())
    serialized = json.dumps(data).lower()
    for forbidden in ('patient', 'email', 'phone', 'mrn', 'oracle', 'hidden_truth', 'actual_outcome'):
        assert forbidden not in serialized
    assert all(f.stock.on_hand != 80 for f in facts.facilities)


def test_bad_fixture_rejects_missing_reserve_accounting(tmp_path, monkeypatch):
    import healthmesh.scenarios.fixture as fixture
    data = json.loads((ROOT / 'fixtures/scenarios/storm-supply-01.json').read_text())
    del data['public_facts']['facilities'][1]['stock']['reserve_accounting']
    (tmp_path / 'storm-supply-01.json').write_text(json.dumps(data))
    monkeypatch.setattr(fixture, 'SCENARIO_DIR', tmp_path)
    with pytest.raises(ScenarioValidationError, match='reserve_accounting'):
        load_scenario('storm-supply-01')


@pytest.mark.parametrize('mutation', ['unknown_field', 'wrong_unit', 'naive_time', 'negative_stock', 'double_reserve', 'extra_facility'])
def test_bad_fixture_fails_closed(tmp_path, monkeypatch, mutation):
    import healthmesh.scenarios.fixture as fixture
    data = json.loads((ROOT / 'fixtures/scenarios/storm-supply-01.json').read_text())
    if mutation == 'unknown_field':
        data['actual_outcome'] = 90
    elif mutation == 'wrong_unit':
        data['public_facts']['product']['base_unit'] = 'bottle'
    elif mutation == 'naive_time':
        data['public_facts']['as_of'] = '2026-10-17T12:00:00'
    elif mutation == 'negative_stock':
        data['public_facts']['facilities'][0]['stock']['on_hand'] = -1
    elif mutation == 'double_reserve':
        data['public_facts']['facilities'][1]['stock']['reserve_accounting'] = 'includes_protected_reserve'
    else:
        data['public_facts']['facilities'].append(data['public_facts']['facilities'][0])
    (tmp_path / 'storm-supply-01.json').write_text(json.dumps(data))
    monkeypatch.setattr(fixture, 'SCENARIO_DIR', tmp_path)
    with pytest.raises(ScenarioValidationError):
        load_scenario('storm-supply-01')


def test_scenario_id_cannot_escape_fixture_directory():
    with pytest.raises(ScenarioValidationError):
        load_scenario('../evaluation/held_out')
