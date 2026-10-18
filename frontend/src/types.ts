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
export interface GroundedWordingDto {
  status: 'SUCCESS' | 'UNAVAILABLE' | 'INVALID_OUTPUT' | 'TIMEOUT';
  source: 'AMD_GENERATED_UNVERIFIED_WORDING' | 'DETERMINISTIC_FALLBACK';
  text: string;
  cited_span_ids: string[];
  receipt_id: string | null;
  receipt: ExtractionReceiptDto['receipt'];
  conflict_id?: string;
  task_id?: string;
}
export interface SessionDto { user: { username: string; role: 'coordinator' | 'reviewer' } | null; csrf_token: string }
export interface AMDHealthDto { status: 'UNAVAILABLE' | 'CONFIGURED'; reason: 'missing_configuration' | 'provider_transport_unverified' | 'token_factory_protocol' }
export interface EvidenceClaimDto {
  id: string; subject_id: string; field: string; value: unknown;
  status: 'OBSERVED_UNVERIFIED' | 'CONFLICTED' | 'HUMAN_VERIFIED' | 'DERIVED_FROM_VERIFIED' | 'UNKNOWN' | 'STALE';
  field_status: 'OBSERVED_UNVERIFIED' | 'CONFLICTED' | 'HUMAN_VERIFIED' | 'DERIVED_FROM_VERIFIED' | 'UNKNOWN' | 'STALE';
  freshness: 'FRESH' | 'STALE'; observed_at: string; receipt_id: string | null;
  source: { id: string; kind: 'SIGNED_FIXTURE' | 'RAW_TEXT' | 'CSV' | 'MANUAL'; observed_at: string; verified: boolean };
  span: { id: string; start: number; end: number; quote: string } | null;
}
export interface EvidenceConflictDto {
  id: string; field_key: string; subject_id: string; field: string; claim_ids: string[];
  conflict_type: 'VALUE_DISAGREEMENT'; state: 'OPEN' | 'RESOLVED';
  resolution_id: string | null; created_at: string;
}
export interface EvidenceDto {
  incident_id: string; revision: number; as_of: string;
  claims: EvidenceClaimDto[]; conflicts: EvidenceConflictDto[];
}
export interface VerificationEffectDto { answer: string; target_branch_status: 'PROVISIONAL' | 'READY_FOR_REVIEW' | 'INFEASIBLE'; branch_statuses: ('PROVISIONAL' | 'READY_FOR_REVIEW' | 'INFEASIBLE')[] }
export interface RankedVerificationDto {
  subject_id: string; facility_name: string; field: 'on_hand' | 'route_status';
  reason_codes: string[]; evidence_ids: string[]; staff_minutes: number; response_delay_minutes: number;
  sensitivity_count: number; sensitivity_per_staff_minute: number; net_value_estimate: number | null;
  possible_effects: VerificationEffectDto[];
}
export interface SelectedVerificationTaskDto {
  id: string; incident_id: string; subject_id: string; field: 'on_hand' | 'route_status';
  selected_snapshot_revision: number; staff_minutes: number; response_delay_minutes: number;
  selected_at: string; due_at: string; need_by: string | null; unit: string | null; evidence_ids: string[];
}
export interface VerificationAnswerInput {
  status: 'CONFIRMED' | 'REJECTED' | 'UNRESOLVED';
  value?: number | 'OPEN' | 'CLOSED'; unit?: string;
  claimed_source: string; expected_revision: number;
}
export interface VerificationResponseDto {
  incident_id: string; new_revision: number; simulated_at: string;
  response_id: string; claim_id: string | null; conflict_id: string | null;
  outcome: 'CONFIRMED' | 'REJECTED' | 'UNRESOLVED' | 'LATE';
  new_plan_run_id?: string | null;
}
export interface HumanAttestationInput {
  value: number | 'OPEN' | 'CLOSED'; unit?: string;
  claimed_source: string; expected_revision: number;
}
export interface HumanAttestationDto {
  incident_id: string; new_revision: number; simulated_at: string;
  attestation_id: string; claim_id: string; conflict_id: string | null;
  new_plan_run_id?: string | null;
}
export interface RankingDto {
  snapshot_id: string; revision: number; tasks: RankedVerificationDto[];
  valuation_mode: 'DETERMINISTIC_SENSITIVITY' | 'WEIGHTED_EXPECTED_LOSS';
  net_value_estimate: number | null; must_verify_before_approval: boolean;
  remaining_staff_minutes: number; remaining_questions: number;
  selected_task?: SelectedVerificationTaskDto | null;
}
