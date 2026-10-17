import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { PlanPreview } from './PlanPreview';

test('unverified branches show their conditions and cannot be reviewed', () => {
  const branch = { id: 'proposal-a', snapshot_id: 'snapshot', revision: 1, status: 'PROVISIONAL' as const,
    allocations: [{ batch_id: 'batch', facility_id: 'facility', facility_name: 'Brook', route_id: 'route', product_id: 'product', base_unit: 'box', quantity: 40, transferable: null }],
    conditions: ['Verify current stock'], checks: [{ subject_id: 'batch', field: 'on_hand', status: 'UNKNOWN' as const, reason: 'Count is stale', evidence_ids: [] }], rejected_reasons: [] };
  render(<PlanPreview plan={{ id: 'run', created_at: '2026-10-17T00:00:00Z', as_of: '2026-10-17T00:00:00Z', status: 'PROVISIONAL', snapshot_id: 'snapshot', revision: 1,
    branches: [branch, { ...branch, id: 'proposal-b' }], mandatory_unverified_facts: branch.checks,
    evidence_ids: [], infeasible_reasons: [], requires_review: true, model_policy_version: 'DETERMINISTIC_PLANNER_V1' }} />);
  expect(screen.getAllByText('Provisional')).toHaveLength(2);
  expect(screen.getAllByText('40 boxes')).toHaveLength(2);
  expect(screen.getAllByText('Verify current stock')).toHaveLength(2);
  expect(screen.getAllByRole('button', { name: /review/i })).toHaveLength(2);
  screen.getAllByRole('button', { name: /review/i }).forEach(button => expect(button).toBeDisabled());
  expect(screen.getByText(/Asia\/Manila/)).toBeInTheDocument();
});
