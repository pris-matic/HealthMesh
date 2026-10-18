"""Conflicting observations stay visible and block readiness until reviewed."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client

from healthmesh.domain.snapshot import build_snapshot
from healthmesh.models import (AIExtractionRun, AIInferenceReceipt, CanonicalFact,
                               InventoryClaim, SourceDocument, SourceSpan)
from healthmesh.planner.service import plan
from healthmesh.scenarios.fixture import load_scenario
from healthmesh.scenarios.service import create_incident

pytestmark = pytest.mark.django_db


@pytest.fixture
def case(django_user_model):
    actor = django_user_model.objects.create_user('conflict-reader')
    actor.groups.add(Group.objects.create(name='coordinator'))
    incident = create_incident(load_scenario('storm-supply-01'), actor)
    snapshot = build_snapshot(incident.id)
    brook = next(batch for batch in snapshot.batches if batch.facility_name == 'Brook')
    client = Client()
    client.force_login(actor)
    return SimpleNamespace(incident=incident, brook=brook, client=client)


def _note_claim(case, field, value, text=None):
    source = SourceDocument.objects.get(incident=case.incident, kind='RAW_TEXT') if text is None else None
    if source is None:
        brook_facility = case.brook.facility_id
        source = SourceDocument.objects.create(incident=case.incident, facility_id=brook_facility,
            kind='RAW_TEXT', observed_at=case.incident.simulated_now,
            original_blob=text.encode(), decoded_text=text)
        span = SourceSpan.objects.create(source=source, start=0, end=len(text))
    else:
        span = next(s for s in SourceSpan.objects.filter(source=source).order_by('start')
                    if 'counted 80 boxes' in s.text)
    return InventoryClaim.objects.create(incident=case.incident, subject_id=case.brook.id,
        field=field, value=value, source=source, span=span, observed_at=source.observed_at,
        status='OBSERVED_UNVERIFIED', basis='Unsigned synthetic count')


def test_seeded_signed_claims_have_precise_spans_in_their_own_sources(case):
    signed = InventoryClaim.objects.filter(incident=case.incident, status='HUMAN_VERIFIED')
    assert signed.exists()
    assert all(claim.span_id and claim.span.source_id == claim.source_id for claim in signed)
    old_count = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='on_hand').claim
    old_reserved = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='reserved').claim
    assert old_count.source_id != old_reserved.source_id
    assert '"on_hand": 95' in old_count.span.text
    assert '"reserved": 25' in old_reserved.span.text
    assert '"batch_id"' not in old_count.span.text
    assert old_count.span.text == old_count.source.decoded_text[old_count.span.start:old_count.span.end]


def test_old_95_new_80_remain_visible_and_conflicted(case):
    from healthmesh.evidence.conflicts import detect_conflicts
    from healthmesh.models import EvidenceConflict

    old = CanonicalFact.objects.get(incident=case.incident, subject_id=case.brook.id, field='on_hand')
    candidate = _note_claim(case, 'on_hand', 80)
    duplicate = _note_claim(case, 'reserved', 25)
    case.incident.refresh_from_db()
    before = case.incident.revision
    conflicts = detect_conflicts(case.incident.id)
    case.incident.refresh_from_db()
    assert case.incident.revision == before + 1
    assert len(conflicts) == EvidenceConflict.objects.filter(incident=case.incident, state='OPEN').count() == 1
    conflict = conflicts[0]
    assert (conflict.subject_id, conflict.field, conflict.conflict_type, conflict.state) == (
        case.brook.id, 'on_hand', 'VALUE_DISAGREEMENT', 'OPEN')
    assert set(conflict.claim_ids) == {str(old.claim_id), str(candidate.id)}
    assert duplicate.id not in conflict.claim_ids
    assert detect_conflicts(case.incident.id)[0].id == conflict.id
    case.incident.refresh_from_db()
    assert case.incident.revision == before + 1

    snapshot = build_snapshot(case.incident.id)
    brook = next(batch for batch in snapshot.batches if batch.id == case.brook.id)
    assert (brook.on_hand.value, brook.on_hand.status, brook.on_hand.evidence_id) == (
        95, 'CONFLICTED', old.claim_id)
    assert {claim.value for claim in snapshot.claims if claim.subject_id == case.brook.id
            and claim.field == 'on_hand'} == {95, 80}
    assert InventoryClaim.objects.get(pk=candidate.id).status == 'OBSERVED_UNVERIFIED'


def test_csv_import_detects_conflict_in_its_single_evidence_revision(case):
    from healthmesh.models import EvidenceConflict
    from healthmesh.sources.service import ingest_source

    actor = case.incident.created_by
    content = ('synthetic,product_code,batch_code,base_unit,reserve_accounting,on_hand,reserved,protected_reserve,observed_at\r\n'
               'true,SIM-MX-01,MX-01,box,separate,80,25,20,2026-10-17T11:00:00Z\r\n').encode()
    before = case.incident.revision
    source = ingest_source(case.incident.id, case.brook.facility_id, 'brook.csv', content, actor)
    case.incident.refresh_from_db()
    assert case.incident.revision == before + 1
    conflict = EvidenceConflict.objects.get(incident=case.incident, field='on_hand')
    assert conflict.state == 'OPEN'
    assert set(conflict.claim_ids) == {str(claim.id) for claim in InventoryClaim.objects.filter(
        incident=case.incident, subject_id=case.brook.id, field='on_hand')}
    assert {claim.value for claim in InventoryClaim.objects.filter(source=source, field='on_hand')} == {80}


def test_reserved_conflict_prevents_ready_brook_branch(case):
    from healthmesh.evidence.conflicts import detect_conflicts

    def with_fresh_brook_90(snapshot):
        batches = tuple(batch.model_copy(update={'on_hand': batch.on_hand.model_copy(update={
            'value': 90, 'status': 'HUMAN_VERIFIED', 'observed_at': snapshot.as_of})})
            if batch.id == case.brook.id else batch for batch in snapshot.batches)
        return snapshot.model_copy(update={'batches': batches})

    before = with_fresh_brook_90(build_snapshot(case.incident.id))
    assert any(branch.status == 'READY_FOR_REVIEW' and
               any(allocation.facility_name == 'Brook' for allocation in branch.allocations)
               for branch in plan(before).branches)
    candidate = _note_claim(case, 'reserved', 45,
        'SYNTHETIC Brook counted 90 boxes, 45 reserved at 2026-10-17T12:00:00Z')
    detect_conflicts(case.incident.id)
    after = with_fresh_brook_90(build_snapshot(case.incident.id))
    brook = next(batch for batch in after.batches if batch.id == case.brook.id)
    assert (brook.reserved.value, brook.reserved.status) == (25, 'CONFLICTED')
    assert InventoryClaim.objects.get(pk=candidate.id).status == 'OBSERVED_UNVERIFIED'
    assert all(branch.status != 'READY_FOR_REVIEW' for branch in plan(after).branches
               if any(allocation.facility_name == 'Brook' for allocation in branch.allocations))


def test_equivalent_claims_cannot_be_persisted_as_value_conflict(case):
    from healthmesh.models import EvidenceConflict

    _note_claim(case, 'reserved', 25)
    claims = InventoryClaim.objects.filter(incident=case.incident, subject_id=case.brook.id, field='reserved')
    with pytest.raises(ValidationError):
        EvidenceConflict.objects.create(incident=case.incident, subject_id=case.brook.id,
            field='reserved', field_key=f'{case.brook.id}:reserved',
            claim_ids=sorted(str(claim.id) for claim in claims),
            conflict_type='VALUE_DISAGREEMENT', state='OPEN')


def test_resolved_conflict_covers_its_manual_resolution_claim(case):
    from healthmesh.evidence.conflicts import detect_conflicts
    from healthmesh.models import EvidenceConflict

    _note_claim(case, 'on_hand', 80)
    conflict = detect_conflicts(case.incident.id)[0]
    text = 'SYNTHETIC Brook manually counted 90 boxes at 2026-10-17T12:00:00Z'
    source = SourceDocument.objects.create(incident=case.incident, facility_id=case.brook.facility_id,
        kind='MANUAL', verified=True, observed_at=case.incident.simulated_now,
        original_blob=text.encode(), decoded_text=text)
    span = SourceSpan.objects.create(source=source, start=0, end=len(text))
    manual = InventoryClaim.objects.create(incident=case.incident, subject_id=case.brook.id,
        field='on_hand', value=90, source=source, span=span,
        observed_at=source.observed_at, status='HUMAN_VERIFIED', basis='Human count')
    conflict.claim_ids = sorted([*conflict.claim_ids, str(manual.id)])
    conflict.state = 'RESOLVED'
    conflict.resolution_id = manual.id
    conflict.save()
    case.incident.refresh_from_db()
    before = case.incident.revision
    assert detect_conflicts(case.incident.id) == []
    case.incident.refresh_from_db()
    assert case.incident.revision == before
    assert EvidenceConflict.objects.get(pk=conflict.id).state == 'RESOLVED'
    _note_claim(case, 'on_hand', 70,
        'SYNTHETIC Brook counted 70 boxes at 2026-10-17T12:00:00Z')
    reopened = detect_conflicts(case.incident.id)
    assert len(reopened) == 1 and reopened[0].id != conflict.id
    assert set(conflict.claim_ids) < set(reopened[0].claim_ids)


def test_evidence_api_is_read_only_and_exposes_quotes_times_and_receipt_ids(case):
    from healthmesh.evidence.conflicts import detect_conflicts

    note = SourceDocument.objects.get(incident=case.incident, kind='RAW_TEXT')
    receipt = AIInferenceReceipt.objects.create(provider='AMD', model_id='DeepSeek-V4-Flash',
        timestamp=datetime(2026, 10, 17, 12, tzinfo=timezone.utc), latency_ms=37,
        status='SUCCESS', request_id='genuine-test-header', input_schema_version='v1',
        output_schema_version='v1')
    run = AIExtractionRun.objects.create(source=note, receipt=receipt, accepted_count=1, rejected_count=0)
    candidate = _note_claim(case, 'on_hand', 80)
    # A second append-only candidate carries extraction provenance.
    extracted = InventoryClaim.objects.create(incident=case.incident, subject_id=case.brook.id,
        field='on_hand', value=80, source=note, span=candidate.span,
        extraction_run=run, observed_at=note.observed_at, status='OBSERVED_UNVERIFIED',
        basis='AMD extracted observation; human verification required')
    detect_conflicts(case.incident.id)
    case.incident.refresh_from_db()
    before = case.incident.revision
    path = f'/api/v1/incidents/{case.incident.id}/evidence'
    first = case.client.get(path)
    assert first.status_code == 200, first.content
    payload = first.json()
    old = next(row for row in payload['claims'] if row['field'] == 'on_hand'
               and row['subject_id'] == str(case.brook.id) and row['value'] == 95)
    new = next(row for row in payload['claims'] if row['id'] == str(extracted.id))
    assert old['source']['id'] != new['source']['id']
    assert old['span']['quote'] == '"on_hand": 95'
    assert 'counted 80 boxes, 25 reserved' in new['span']['quote']
    assert old['observed_at'] and new['observed_at']
    assert old['status'] == 'HUMAN_VERIFIED' and old['field_status'] == 'CONFLICTED'
    assert new['status'] == 'OBSERVED_UNVERIFIED' and new['field_status'] == 'CONFLICTED'
    assert old['freshness'] == 'STALE' and new['freshness'] == 'FRESH'
    assert new['receipt_id'] == str(receipt.id)
    assert payload['conflicts'][0]['state'] == 'OPEN'
    assert not any(key in str(payload) for key in ('original_blob', 'raw_content', 'api_key', 'prompt'))
    assert case.client.get(path).json() == payload
    case.incident.refresh_from_db()
    assert case.incident.revision == before
