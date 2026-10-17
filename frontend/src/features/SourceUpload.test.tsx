import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from '../App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function server(failure = false) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    let payload: unknown;
    let ok = true;
    if (url.endsWith('/session')) payload = { user: { username: 'demo', role: 'coordinator' }, csrf_token: 'token' };
    else if (url.endsWith('/scenarios')) payload = { id: 'incident', seed: 'storm-supply-01', revision: 1, synthetic: true, facilities: [{ id: 'brook', name: 'Brook' }] };
    else if (url.endsWith('/plan')) payload = { id: 'run', created_at: '2026-10-17T12:00:00Z', as_of: '2026-10-17T12:00:00Z', status: 'PROVISIONAL', snapshot_id: 'snapshot', revision: 1, branches: [], mandatory_unverified_facts: [], evidence_ids: [], infeasible_reasons: [], requires_review: true, model_policy_version: 'V1' };
    else if (url.endsWith('/sources')) {
      const form = options?.body as FormData;
      if (!(form instanceof FormData) || form.get('incident_id') !== 'incident' || form.get('facility_id') !== 'brook' || !(form.get('file') instanceof File) || (options?.headers as Record<string, string>)['X-CSRFToken'] !== 'token') throw new Error('Invalid upload request');
      ok = !failure;
      payload = failure ? { code: 'invalid_source', message: 'Only synthetic supply data is accepted.', field_errors: { file: ['Patient data is prohibited.'] }, request_id: 'request' } : { id: 'source-123', sha256: 'a'.repeat(64), kind: 'RAW_TEXT', observed_at: '2026-10-17T12:00:00Z', duplicate: false };
    } else throw new Error(`Unexpected URL ${url}`);
    return { ok, json: async () => payload };
  }));
}

async function submit() {
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'Load scenario and plan' }));
  await screen.findByRole('option', { name: 'Brook' });
  fireEvent.change(screen.getByLabelText('Facility'), { target: { value: 'brook' } });
  fireEvent.change(screen.getByLabelText('Source file'), { target: { files: [new File(['SYNTHETIC 80 boxes'], 'note.txt', { type: 'text/plain' })] } });
  const button = screen.getByRole('button', { name: 'Import source' });
  expect(button).toBeEnabled();
  // JSDOM does not update native file validity from fireEvent's files property.
  fireEvent.submit(button.closest('form')!);
}

test('coordinator uploads source with scope and CSRF and sees immutable source ID', async () => {
  server();
  await submit();
  expect(await screen.findByText(/source-123/)).toBeInTheDocument();
  expect(screen.getByText(/Evidence remains unverified/i)).toBeInTheDocument();
  expect(screen.getByText(/saved plan/i)).toBeInTheDocument();
});

test('upload failure explains rejection without reporting success', async () => {
  server(true);
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Only synthetic supply data is accepted.');
  expect(screen.getByRole('alert')).toHaveTextContent('Patient data is prohibited.');
  expect(screen.queryByText(/source-123/)).not.toBeInTheDocument();
});
