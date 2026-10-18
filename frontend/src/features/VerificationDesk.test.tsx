import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { VerificationDesk } from './VerificationDesk';
import type { RankingDto } from '../types';
import App from '../App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('shows competing questions, effort, delay, and answer-specific branch effects', () => {
  const ranking: RankingDto = {
    valuation_mode: 'DETERMINISTIC_SENSITIVITY', net_value_estimate: null,
    must_verify_before_approval: true, snapshot_id: 'incident:1', revision: 1,
    remaining_staff_minutes: 10, remaining_questions: 1,
    tasks: [
      { subject_id: 'brook-batch', facility_name: 'Brook', field: 'on_hand', reason_codes: ['DECISION_SENSITIVE'],
        evidence_ids: ['signed-95', 'candidate-80'], staff_minutes: 3, response_delay_minutes: 10,
        sensitivity_count: 2, sensitivity_per_staff_minute: 2 / 3, net_value_estimate: null,
        possible_effects: [
          { answer: '70', target_branch_status: 'INFEASIBLE', branch_statuses: ['INFEASIBLE', 'PROVISIONAL'] },
          { answer: '90', target_branch_status: 'READY_FOR_REVIEW', branch_statuses: ['READY_FOR_REVIEW', 'PROVISIONAL'] },
        ] },
      { subject_id: 'cedar-route', facility_name: 'Cedar', field: 'route_status', reason_codes: ['DECISION_SENSITIVE'],
        evidence_ids: ['route-source'], staff_minutes: 5, response_delay_minutes: 20,
        sensitivity_count: 1, sensitivity_per_staff_minute: 1 / 5, net_value_estimate: null,
        possible_effects: [
          { answer: 'CLOSED', target_branch_status: 'INFEASIBLE', branch_statuses: ['PROVISIONAL', 'INFEASIBLE'] },
          { answer: 'OPEN', target_branch_status: 'READY_FOR_REVIEW', branch_statuses: ['PROVISIONAL', 'READY_FOR_REVIEW'] },
        ] },
    ],
  };
  const select = vi.fn();
  render(<VerificationDesk ranking={ranking} onSelect={select} canSelect />);
  expect(screen.getByText(/No probabilities are assigned/)).toBeInTheDocument();
  expect(screen.getByText(/Mandatory verification before approval/)).toBeInTheDocument();
  const brook = screen.getByRole('article', { name: /Brook on hand/i });
  expect(within(brook).getByText(/3 staff minutes/)).toBeInTheDocument();
  expect(within(brook).getByText(/10 minute response delay/)).toBeInTheDocument();
  expect(within(brook).getByText(/70.*Brook branch: Infeasible/i)).toBeInTheDocument();
  expect(within(brook).getByText(/90.*Brook branch: Ready for review/i)).toBeInTheDocument();
  const cedar = screen.getByRole('article', { name: /Cedar route status/i });
  expect(within(cedar).getByText(/5 staff minutes/)).toBeInTheDocument();
  expect(within(cedar).getByText(/OPEN.*Cedar branch: Ready for review/i)).toBeInTheDocument();
  fireEvent.click(within(brook).getByRole('button', { name: /Select Brook question/i }));
  expect(select).toHaveBeenCalledWith('brook-batch', 'on_hand');
});

test('explicit public weights show synthetic expected-loss value rather than no-probability wording', () => {
  const ranking: RankingDto = { snapshot_id: 'incident:1', revision: 1,
    tasks: [], valuation_mode: 'WEIGHTED_EXPECTED_LOSS', net_value_estimate: 1998.23,
    must_verify_before_approval: true, remaining_staff_minutes: 10, remaining_questions: 1 };
  render(<VerificationDesk ranking={ranking} onSelect={() => {}} canSelect={false} />);
  expect(screen.getByText(/1998.23 synthetic loss units/)).toBeInTheDocument();
  expect(screen.queryByText(/No probabilities are assigned/)).not.toBeInTheDocument();
});

test('selected task shows deterministic fallback and requests AMD draft only on click', async () => {
  const ranking: RankingDto = { snapshot_id: 'incident:3', revision: 3,
    tasks: [], valuation_mode: 'DETERMINISTIC_SENSITIVITY', net_value_estimate: null,
    must_verify_before_approval: true, remaining_staff_minutes: 7, remaining_questions: 0,
    selected_task: { id: 'task-1', incident_id: 'incident', subject_id: 'brook', field: 'on_hand',
      selected_snapshot_revision: 3, staff_minutes: 3, response_delay_minutes: 10,
      selected_at: '2026-10-17T12:00:00Z', due_at: '2026-10-17T12:10:00Z',
      need_by: '2026-10-18T12:00:00Z', unit: 'box',
      evidence_ids: ['signed-95', 'candidate-80'] } };
  const draft = vi.fn(async () => ({ task_id: 'task-1', status: 'SUCCESS' as const,
    source: 'AMD_GENERATED_UNVERIFIED_WORDING' as const,
    text: 'Can you confirm the Brook on-hand box count from 95 boxes [A] and 80 boxes [B]?',
    cited_span_ids: ['signed-span', 'note-span'], receipt_id: 'question-receipt',
    receipt: { provider: 'AMD' as const, model_id: 'DeepSeek-V4-Flash',
      timestamp: '2026-10-17T12:00:00Z', latency_ms: 123, status: 'SUCCESS' as const,
      request_id: null, input_schema_version: 'question-v1', output_schema_version: 'grounding-v1' } }));
  render(<VerificationDesk ranking={ranking} onSelect={() => {}} canSelect={false}
    selectedFallback={{ status: 'UNAVAILABLE', source: 'DETERMINISTIC_FALLBACK',
      text: 'Please confirm Brook on-hand box count using [A] and [B]?',
      cited_span_ids: ['signed-span', 'note-span'], receipt_id: null, receipt: null }}
    onDraft={draft} />);
  expect(screen.getByText(/Deterministic question/)).toBeInTheDocument();
  expect(draft).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Ask AMD to draft selected question/i }));
  expect(await screen.findByText(/AI generated wording, unverified/i)).toBeInTheDocument();
  expect(screen.getByText(/question-receipt/)).toBeInTheDocument();
  expect(draft).toHaveBeenCalledWith('task-1');
});

test('coordinator confirms the selected count with a named source and current revision', async () => {
  const ranking: RankingDto = { snapshot_id: 'incident:4', revision: 4,
    tasks: [], valuation_mode: 'DETERMINISTIC_SENSITIVITY', net_value_estimate: null,
    must_verify_before_approval: true, remaining_staff_minutes: 7, remaining_questions: 0,
    selected_task: { id: 'task-1', incident_id: 'incident', subject_id: 'brook', field: 'on_hand',
      selected_snapshot_revision: 3, staff_minutes: 3, response_delay_minutes: 10,
      selected_at: '2026-10-17T12:00:00Z', due_at: '2026-10-17T12:10:00Z',
      need_by: '2026-10-18T12:00:00Z', unit: 'box',
      evidence_ids: ['signed-95', 'candidate-80'] } };
  const respond = vi.fn(async () => ({ incident_id: 'incident', new_revision: 5,
    simulated_at: '2026-10-17T12:10:00Z', response_id: 'response-1',
    claim_id: 'claim-90', conflict_id: 'conflict-1', outcome: 'CONFIRMED' as const }));
  render(<VerificationDesk ranking={ranking} onSelect={() => {}} canSelect={false}
    canRespond onRespond={respond} />);
  fireEvent.change(screen.getByLabelText('Confirmed value'), { target: { value: '90' } });
  fireEvent.change(screen.getByLabelText('Named synthetic source'), { target: { value: 'Brook physical recount' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record response' }));
  expect(await screen.findByText(/Human verified claim claim-90/)).toBeInTheDocument();
  expect(respond).toHaveBeenCalledWith('task-1', {
    status: 'CONFIRMED', value: 90, unit: 'box',
    claimed_source: 'Brook physical recount', expected_revision: 4,
  });
});

test('late and unresolved responses are recorded without saying they verified a fact', async () => {
  const ranking: RankingDto = { snapshot_id: 'incident:4', revision: 4,
    tasks: [], valuation_mode: 'DETERMINISTIC_SENSITIVITY', net_value_estimate: null,
    must_verify_before_approval: true, remaining_staff_minutes: 7, remaining_questions: 0,
    selected_task: { id: 'task-1', incident_id: 'incident', subject_id: 'brook', field: 'on_hand',
      selected_snapshot_revision: 3, staff_minutes: 3, response_delay_minutes: 10,
      selected_at: '2026-10-17T12:00:00Z', due_at: '2026-10-17T12:10:00Z',
      need_by: '2026-10-18T12:00:00Z', unit: 'box',
      evidence_ids: ['signed-95', 'candidate-80'] } };
  const respond = vi.fn(async () => ({ incident_id: 'incident', new_revision: 5,
    simulated_at: '2026-10-18T12:10:00Z', response_id: 'response-late',
    claim_id: null, conflict_id: null, outcome: 'LATE' as const }));
  render(<VerificationDesk ranking={ranking} onSelect={() => {}} canSelect={false}
    canRespond onRespond={respond} />);
  fireEvent.change(screen.getByLabelText('Response status'), { target: { value: 'UNRESOLVED' } });
  expect(screen.queryByLabelText('Confirmed value')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Named synthetic source'), { target: { value: 'Brook recount could not be completed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record response' }));
  expect(await screen.findByText(/Recorded late/)).toBeInTheDocument();
  expect(screen.getByText(/No verified fact was added/)).toBeInTheDocument();
  expect(respond).toHaveBeenCalledWith('task-1', {
    status: 'UNRESOLVED', claimed_source: 'Brook recount could not be completed', expected_revision: 4,
  });
  expect(screen.queryByText(/Human verified claim/)).not.toBeInTheDocument();
});

test('recorded coordinator response refreshes evidence, ranking, and plan for the new revision', async () => {
  let revision = 1;
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    calls.push(url);
    let payload: unknown;
    if (url.endsWith('/session')) payload = { user: { username: 'coordinator', role: 'coordinator' }, csrf_token: 'csrf' };
    else if (url.endsWith('/amd/health')) payload = { status: 'UNAVAILABLE', reason: 'missing_configuration' };
    else if (url.endsWith('/scenarios')) payload = { id: 'incident', seed: 'storm-supply-01', revision: 1,
      synthetic: true, facilities: [{ id: 'brook', name: 'Brook' }] };
    else if (url.endsWith('/plan')) payload = { id: `run-${revision}`, created_at: '2026-10-17T12:00:00Z',
      as_of: '2026-10-17T12:00:00Z', status: 'PROVISIONAL', snapshot_id: `incident:${revision}`,
      revision, branches: [], mandatory_unverified_facts: [], evidence_ids: [], infeasible_reasons: [],
      requires_review: true, model_policy_version: 'DETERMINISTIC_PLANNER_V1' };
    else if (url.endsWith('/verification-tasks')) payload = { snapshot_id: `incident:${revision}`, revision,
      tasks: [], valuation_mode: 'DETERMINISTIC_SENSITIVITY', net_value_estimate: null,
      must_verify_before_approval: true, remaining_staff_minutes: 7, remaining_questions: 0,
      selected_task: { id: 'task-1', incident_id: 'incident', subject_id: 'brook', field: 'on_hand',
        selected_snapshot_revision: 1, staff_minutes: 3, response_delay_minutes: 10,
        selected_at: '2026-10-17T12:00:00Z', due_at: '2026-10-17T12:10:00Z',
        need_by: '2026-10-18T12:00:00Z', unit: 'box', evidence_ids: ['signed-95'] } };
    else if (url.endsWith('/evidence')) payload = { incident_id: 'incident', revision,
      as_of: '2026-10-17T12:10:00Z', claims: [], conflicts: [] };
    else if (url.endsWith('/verification-tasks/task-1/respond')) {
      expect(options?.method).toBe('POST');
      expect((options?.headers as Record<string, string>)['X-CSRFToken']).toBe('csrf');
      expect(JSON.parse(options?.body as string)).toEqual({ status: 'CONFIRMED', value: 90,
        unit: 'box', claimed_source: 'Brook physical recount', expected_revision: 1 });
      revision = 2;
      payload = { incident_id: 'incident', new_revision: 2, simulated_at: '2026-10-17T12:10:00Z',
        response_id: 'response-1', claim_id: 'claim-90', conflict_id: 'conflict-1', outcome: 'CONFIRMED' };
    } else throw new Error(`Unexpected URL ${url}`);
    return { ok: true, json: async () => payload };
  }));
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'Load scenario and plan' }));
  fireEvent.click(await screen.findByRole('button', { name: 'View evidence' }));
  expect(await screen.findByText(/Incident revision 1/)).toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText('Confirmed value'), { target: { value: '90' } });
  fireEvent.change(screen.getByLabelText('Named synthetic source'), { target: { value: 'Brook physical recount' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record response' }));
  expect(await screen.findByText(/Human verified claim claim-90/)).toBeInTheDocument();
  expect(await screen.findByText(/Incident revision 2/)).toBeInTheDocument();
  await waitFor(() => expect(calls.filter(url => url.endsWith('/plan'))).toHaveLength(2));
  expect(calls.filter(url => url.endsWith('/verification-tasks'))).toHaveLength(2);
  expect(calls.filter(url => url.endsWith('/evidence'))).toHaveLength(2);
});
