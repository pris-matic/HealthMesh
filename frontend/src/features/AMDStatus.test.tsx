import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import App from '../App';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('signed-in demo visibly reports AMD unavailable', async () => {
  vi.stubGlobal('fetch', async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/session')) return { user: { username: 'demo', role: 'coordinator' }, csrf_token: 'token' };
    if (url.endsWith('/amd/health')) return { status: 'UNAVAILABLE', reason: 'missing_configuration' };
    throw new Error(`Unexpected URL ${url}`);
  } }));
  render(<App />);
  expect(await screen.findByText(/AMD inference: UNAVAILABLE/)).toBeInTheDocument();
  expect(screen.getByText(/AI extraction and grounded wording are unavailable/)).toBeInTheDocument();
});

test('health request failure never implies AMD availability', async () => {
  vi.stubGlobal('fetch', async (url: string) => {
    if (url.endsWith('/session')) return { ok: true, json: async () => ({ user: { username: 'demo', role: 'coordinator' }, csrf_token: 'token' }) };
    throw new Error('network failure');
  });
  render(<App />);
  expect(await screen.findByText(/AMD inference: UNAVAILABLE/)).toBeInTheDocument();
});

test('configured transport is shown as configured without claiming a successful call', async () => {
  vi.stubGlobal('fetch', async (url: string) => ({ ok: true, json: async () => {
    if (url.endsWith('/session')) return { user: { username: 'demo', role: 'coordinator' }, csrf_token: 'token' };
    if (url.endsWith('/amd/health')) return { status: 'CONFIGURED', reason: 'token_factory_protocol' };
    throw new Error(`Unexpected URL ${url}`);
  } }));
  render(<App />);
  expect(await screen.findByText(/AMD inference: CONFIGURED/)).toBeInTheDocument();
  expect(screen.getByText(/reports local settings, not a successful AMD call/i)).toBeInTheDocument();
});
