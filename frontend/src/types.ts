export interface Check { subject_id: string | null; field: string; status: 'PASS' | 'FAIL' | 'UNKNOWN'; reason: string; evidence_ids: string[] }
export interface Allocation { batch_id: string; facility_id: string; facility_name: string; route_id: string | null; product_id: string; base_unit: string; quantity: number; transferable: number | null }
export interface Branch { id: string; snapshot_id: string; revision: number; allocations: Allocation[]; status: 'PROVISIONAL' | 'READY_FOR_REVIEW' | 'INFEASIBLE'; conditions: string[]; checks: Check[]; rejected_reasons: string[] }
export interface PlanDto { id: string; created_at: string; as_of: string; status: 'PROVISIONAL' | 'READY_FOR_REVIEW' | 'BLOCKED_INSUFFICIENT_EVIDENCE' | 'NO_FEASIBLE_OPTION'; snapshot_id: string; revision: number; branches: Branch[]; mandatory_unverified_facts: Check[]; evidence_ids: string[]; infeasible_reasons: string[]; requires_review: true; model_policy_version: string }
export interface FacilityDto { id: string; name: string }
export interface ScenarioDto { id: string; seed: string; revision: number; synthetic: true; facilities: FacilityDto[] }
export interface SourceDto { id: string; sha256: string; kind: 'CSV' | 'RAW_TEXT'; observed_at: string; duplicate: boolean }
export interface ExtractionReceiptDto {
  source_id: string;
  status: 'SUCCESS' | 'UNAVAILABLE' | 'INVALID_OUTPUT' | 'TIMEOUT';
  accepted_count: number;
  rejected_count: number;
  receipt_id: string | null;
  receipt: { provider: 'AMD'; model_id: string; timestamp: string; latency_ms: number;
    status: 'SUCCESS' | 'INVALID_OUTPUT'; request_id: string | null;
    input_schema_version: string; output_schema_version: string } | null;
  claims: { id: string; field: string; value: number | string; status: 'OBSERVED_UNVERIFIED'; source_span_id: string }[];
  new_revision: number | null;
}
export interface SessionDto { user: { username: string; role: 'coordinator' | 'reviewer' } | null; csrf_token: string }
export interface AMDHealthDto { status: 'UNAVAILABLE' | 'CONFIGURED'; reason: 'missing_configuration' | 'provider_transport_unverified' | 'token_factory_protocol' }
