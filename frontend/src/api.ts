import type { PlanDto, ScenarioDto, SessionDto } from './types';
let csrfToken = '';
async function request<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: data === undefined ? undefined : JSON.stringify(data) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || 'The request failed. Please try again.');
  return payload as T;
}
export async function session(method = 'GET', data?: unknown): Promise<SessionDto> {
  const result = await request<SessionDto>('/session', method, data);
  csrfToken = result.csrf_token;
  return result;
}
export const createScenario = (): Promise<ScenarioDto> => request('/scenarios', 'POST', { seed: 'storm-supply-01' });
export const requestPlan = (id: string): Promise<PlanDto> => request(`/incidents/${id}/plan`, 'POST');
