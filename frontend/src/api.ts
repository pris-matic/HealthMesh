import type { AMDHealthDto, PlanDto, ScenarioDto, SessionDto, SourceDto } from './types';
let csrfToken = '';
async function request<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { method, credentials: 'same-origin',
    headers: { ...(data instanceof FormData ? {} : { 'Content-Type': 'application/json' }), 'X-CSRFToken': csrfToken },
    body: data instanceof FormData ? data : data === undefined ? undefined : JSON.stringify(data) });
  const payload = await response.json();
  if (!response.ok) {
    const details = Object.values(payload.field_errors ?? {}).flat().filter(value => typeof value === 'string');
    throw new Error([payload.message || 'The request failed. Please try again.', ...details].join(' '));
  }
  return payload as T;
}
export async function session(method = 'GET', data?: unknown): Promise<SessionDto> {
  const result = await request<SessionDto>('/session', method, data);
  csrfToken = result.csrf_token;
  return result;
}
export const createScenario = (): Promise<ScenarioDto> => request('/scenarios', 'POST', { seed: 'storm-supply-01' });
export const amdHealth = (): Promise<AMDHealthDto> => request('/amd/health');
export const requestPlan = (id: string): Promise<PlanDto> => request(`/incidents/${id}/plan`, 'POST');
export function uploadSource(incidentId: string, facilityId: string, file: File): Promise<SourceDto> {
  const data = new FormData();
  data.append('incident_id', incidentId);
  data.append('facility_id', facilityId);
  data.append('file', file);
  return request('/sources', 'POST', data);
}
