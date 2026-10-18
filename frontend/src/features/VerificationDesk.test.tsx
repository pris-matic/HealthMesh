import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { VerificationDesk } from './VerificationDesk';
import type { RankingDto } from '../types';

afterEach(() => { cleanup(); });

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
