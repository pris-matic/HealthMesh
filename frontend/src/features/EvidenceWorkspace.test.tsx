import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { EvidenceWorkspace } from './EvidenceWorkspace';
import type { EvidenceDto } from '../types';
import App from '../App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('conflicting Brook counts show both source quotes and verification states side by side', () => {
  const evidence: EvidenceDto = {
    incident_id: 'incident', revision: 3, as_of: '2026-10-17T12:00:00Z',
    claims: [
      { id: 'signed-95', subject_id: 'brook-batch', field: 'on_hand', value: 95,
        status: 'HUMAN_VERIFIED', field_status: 'CONFLICTED', freshness: 'STALE',
        observed_at: '2026-10-16T07:00:00Z', receipt_id: null,
        source: { id: 'old-source', kind: 'SIGNED_FIXTURE', observed_at: '2026-10-16T07:00:00Z', verified: true },
        span: { id: 'signed-span', start: 400, end: 413, quote: '"on_hand": 95' } },
      { id: 'candidate-80', subject_id: 'brook-batch', field: 'on_hand', value: 80,
        status: 'OBSERVED_UNVERIFIED', field_status: 'CONFLICTED', freshness: 'FRESH',
        observed_at: '2026-10-17T11:00:00Z', receipt_id: 'amd-receipt',
        source: { id: 'note-source', kind: 'RAW_TEXT', observed_at: '2026-10-17T11:00:00Z', verified: false },
        span: { id: 'note-span', start: 20, end: 49, quote: 'Brook counted 80 boxes, 25 reserved' } },
    ],
    conflicts: [{ id: 'conflict', field_key: 'brook-batch:on_hand', subject_id: 'brook-batch',
      field: 'on_hand', claim_ids: ['signed-95', 'candidate-80'],
      conflict_type: 'VALUE_DISAGREEMENT', state: 'OPEN', resolution_id: null,
      created_at: '2026-10-17T12:00:00Z' }],
  };
  render(<EvidenceWorkspace evidence={evidence} />);
  const group = screen.getByRole('region', { name: /on hand conflict/i });
  expect(within(group).getByText('95 boxes')).toBeInTheDocument();
  expect(within(group).getByText('80 boxes')).toBeInTheDocument();
  expect(within(group).getByText('"on_hand": 95')).toBeInTheDocument();
  expect(within(group).getByText('Brook counted 80 boxes, 25 reserved')).toBeInTheDocument();
  expect(within(group).getByText(/Human verified/)).toBeInTheDocument();
  expect(within(group).getByText(/Observed, unverified/)).toBeInTheDocument();
  expect(within(group).getAllByText('Conflicted')).toHaveLength(2);
  expect(within(group).getByText(/amd-receipt/)).toBeInTheDocument();
  expect(within(group).getAllByText(/2026/).length).toBeGreaterThanOrEqual(2);
  expect(screen.queryByText(/original_blob|raw_content|prompt/)).not.toBeInTheDocument();
});

test('loaded incident opens the read-only evidence workspace from the main screen', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    let payload: unknown;
    if (url.endsWith('/session')) payload = { user: { username: 'demo', role: 'coordinator' }, csrf_token: 'token' };
    else if (url.endsWith('/amd/health')) payload = { status: 'UNAVAILABLE', reason: 'missing_configuration' };
    else if (url.endsWith('/scenarios')) payload = { id: 'incident', seed: 'storm-supply-01', revision: 1,
      synthetic: true, facilities: [{ id: 'brook', name: 'Brook' }] };
    else if (url.endsWith('/plan')) payload = { id: 'run', created_at: '2026-10-17T12:00:00Z', as_of: '2026-10-17T12:00:00Z',
      status: 'PROVISIONAL', snapshot_id: 'incident:1', revision: 1, branches: [],
      mandatory_unverified_facts: [], evidence_ids: [], infeasible_reasons: [],
      requires_review: true, model_policy_version: 'DETERMINISTIC_PLANNER_V1' };
    else if (url.endsWith('/evidence')) payload = { incident_id: 'incident', revision: 2,
      as_of: '2026-10-17T12:00:00Z', claims: [], conflicts: [] };
    else throw new Error(`Unexpected URL ${url}`);
    return { ok: true, json: async () => payload };
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'Load scenario and plan' }));
  fireEvent.click(await screen.findByRole('button', { name: 'View evidence' }));
  expect(await screen.findByText(/Incident revision 2/)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Evidence workspace' })).toBeInTheDocument();
});
