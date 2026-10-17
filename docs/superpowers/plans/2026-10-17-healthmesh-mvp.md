# HealthMesh MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. **This document is planning only; it authorizes no implementation.**

**Goal:** Deliver a synthetic, source-backed HealthMesh demo in which real AMD extraction produces unverified claims, a human resolves decision-critical evidence, deterministic planning changes, and a reviewer can approve or reject only a valid simulation proposal.

**Architecture:** Build one Django/DRF modular monolith with an immutable evidence ledger and versioned incident snapshots. Keep AMD inference behind `AMDInferenceAdapter` for extraction and grounded wording; pure Python services calculate coverage, rank verification, enumerate plans, and enforce review gates. Use a React/TypeScript/Vite client that exposes evidence, branches, uncertainty, and audit history.

**Tech Stack:** Python 3.12, Django, Django REST Framework, pytest/pytest-django, PostgreSQL for production (SQLite for local development), React, TypeScript, Vite, Vitest, Playwright. Pin compatible dependency versions and commit lockfiles during implementation; this repo has none yet.

**Spec:** [`HealthMesh_Architecture_Design.md`](../../../HealthMesh_Architecture_Design.md), especially §§5–18. The specific P0/P1/P2 order in the session brief resolves priority conflicts in the design.

## Global Constraints

- Three fictional facilities, one fictional product, one base unit, one urgent request; all public/demo data is synthetic. No patient data, clinical advice, medication substitution, dosing, real procurement, or physical transfer execution.
- Python 3.12 + Django/DRF backend, React + TypeScript + Vite frontend, PostgreSQL production target, SQLite local option, one modular monolith.
- AMD-hosted inference is limited to candidate extraction and grounded language. The backend owns numeric calculations, ranking, planning, permissions, and approval. Never invent an AMD endpoint, protocol, model ID, receipt, or successful call.
- AI claims begin `OBSERVED_UNVERIFIED`; source provenance is mandatory for decision-critical claims. Unknown mandatory facts fail closed. Revisions, plans, decisions, and audit events remain historically visible.
- `transferable = max(0, on_hand - reserved - protected_reserve)` only for verified, fresh, unexpired, usable batches, with `reserved` explicitly excluding protected reserve. A provisional branch is never approvable.
- UTC timestamps and UUIDs internally; explicit local display timezone. All mutating API routes require an authenticated synthetic actor and appropriate role. Responses use `{code,message,field_errors,request_id}` for errors.
- Every plan/review uses a frozen incident revision and a fresh snapshot check. A second or stale review fails transactionally. No endpoint dispatches an order.
- P0 must contain a real AMD extraction and the two grounded AMD operations required by §8. If event configuration is unavailable, keep the visible `UNAVAILABLE` state and leave the live acceptance gate open; do not label a stub as a pass. P1 adds coverage and evaluation; P2 starts only after P0 and P1 work.

## Repository Baseline and Reuse

The repository is `C:\IZECUBES\AMD HealthMesh\HealthMesh` on `dev` at `09dbea5`. HEAD contains only `.gitignore` and `README.md` (`# HealthMesh`); `HealthMesh_Architecture_Design.md` is untracked. There is no application code, package manifest, environment file, fixture, test, CI, or deployment configuration. Reuse the architecture document as the specification, retain the README and extend it, and retain the useful Python ignore rules while adding frontend/build/upload exclusions. There is no working frontend/backend code to migrate or replace and no code-level architecture conflict. Preserve the untracked design document; do not fold its contents into generated code or silently change it.

## Contract Map and Planned Files

All paths below are relative to the repository root. `backend/healthmesh` is one Django app with focused service packages; `backend/healthmesh/models.py` imports model classes from `db_models/*.py` so Django discovers them. Generated migrations are checked in. The public fixture is separate from `fixtures/evaluation/held_out.json`, which only the evaluator reads.

| File or family | Single responsibility |
|---|---|
| `fixtures/scenarios/storm-supply-01.json`, `fixtures/sources/brook-note.txt` | Fixed public scenario and free-text input, never hidden oracle truth |
| `backend/healthmesh/db_models/{core,sources,planning,workflow,forecast,evaluation}.py` | Relational records added only when the matching vertical slice needs them |
| `backend/healthmesh/domain/types.py`, `domain/snapshot.py` | Typed immutable planning input and projection from versioned facts |
| `backend/healthmesh/{sources,ai_gateway,evidence,planner,workflow,forecast,evaluation}/*.py` | One module per import, validation, inference, decision, or policy operation |
| `frontend/src/{api,types}.ts`, `frontend/src/features/*` | Typed API boundary and focused evidence/plan/review/results views |
| `backend/healthmesh/tests/*`, `frontend/src/**/*.test.tsx`, `frontend/e2e/*` | Unit, API, UI, live-provider, and blocked-path evidence |

Stable cross-task interfaces (new names are plan decisions, not existing code):

```text
load_scenario(scenario_id: str) -> ScenarioSeed
create_incident(seed: ScenarioSeed, actor: User) -> Incident
build_snapshot(incident_id: UUID) -> IncidentSnapshot
plan(snapshot: IncidentSnapshot) -> PlanResult
validate_proposal(snapshot: IncidentSnapshot, branch: PlanBranch) -> list[ConstraintCheck]
ingest_source(incident_id: UUID, facility_id: UUID, filename: str, content: bytes, actor: User) -> SourceDocument
validate_extraction(raw: object, allowed_spans: Sequence[SourceSpan], base_unit: str) -> ValidatedClaims
AMDInferenceAdapter.extract_claims(text: str, allowed_spans: Sequence[SourceSpan], schema_version: str) -> ExtractionResult
AMDInferenceAdapter.explain_conflict(conflict_id: UUID, evidence: Sequence[SourceSpan]) -> GroundedExplanation
AMDInferenceAdapter.draft_verification(task: VerificationTask, evidence: Sequence[SourceSpan]) -> SuggestedMessage
extract_source(source_id: UUID, actor: User) -> ExtractionResult
detect_conflicts(incident_id: UUID) -> list[EvidenceConflict]
rank_verifications(snapshot: IncidentSnapshot, plan_result: PlanResult, budget: VerificationBudget) -> RankingResult
respond_to_verification(task_id: UUID, answer: VerificationAnswer, actor: User) -> EvidenceRevision
replan_after_evidence_change(incident_id: UUID, expected_prior_revision: int) -> PlanRun
review_proposal(proposal_id: UUID, decision: ReviewAction, expected_revision: int, actor: User) -> ReviewDecision
calculate_coverage(snapshot: IncidentSnapshot, valid_days: int = 14) -> CoverageResult
run_evaluation(seed: int, policies: Sequence[PolicyName]) -> SimulationRun
```

`IncidentSnapshot` has `incident_id`, `revision`, `as_of`, request, batches, routes, claims, and source IDs. Each planning fact has a value, `OBSERVED_UNVERIFIED | CONFLICTED | HUMAN_VERIFIED | DERIVED_FROM_VERIFIED | UNKNOWN | STALE`, observed time, and evidence ID. `PlanResult` has `status` (`PROVISIONAL | READY_FOR_REVIEW | BLOCKED_INSUFFICIENT_EVIDENCE | NO_FEASIBLE_OPTION`), `input_snapshot_id`, `input_revision`, `branches`, `mandatory_unverified_facts`, `evidence_ids`, `infeasible_reasons`, `requires_review=true`, and `model_policy_version`. `PlanBranch` has allocations, explicit conditions, checks, status (`PROVISIONAL | READY_FOR_REVIEW | INFEASIBLE`), and rejected reasons. An aggregate no-option result may have zero branches. These are shared Python contracts; `frontend/src/types.ts` mirrors only serialized API fields.

**Fixture choices to lock in Task 1 (implementation decisions, not claimed as existing data):** scenario `storm-supply-01`, `as_of=2026-10-17T12:00:00Z`, synthetic need-by `2026-10-18T12:00:00Z`, fictional product `SIM-MX-01` in `box`, Aster request 40. Brook's signed old count is 95 from October 15 and is stale; its October 17 unsigned **source text** says 80 on hand and 25 reserved. The text is not seeded as an extracted claim before the real AMD flow. Brook's separately signed 25 reserved, protected reserve 20, unexpired batch (`expires_on=2026-12-31`), usable storage, and open two-hour route are verified fixture facts. Cedar has fresh verified 55 on hand, zero reserved, protected reserve 10, unexpired/usable stock, and an unknown route. A human count of Brook 90 with the previously verified 25 reserved yields 45 transferable and makes a 40-unit branch possible. A blocked variant confirms Brook 70 and Cedar's route closed. The note variant changes 25 reserved to 45, creating a conflict and changing branch status. Store fixed daily demand, freshness threshold (24 hours), transport deadline, 10 staff-minute/one-question verification budget, and any public plausible-world weights in this fixture; mark the weights synthetic. The only unit alias is `boxes` → `box`. Store hidden truth separately under `fixtures/evaluation/`.

**Synthetic decision policy to share across Tasks 10 and 18:** `LOSS_POLICY_V1 = 100 × unmet_units_by_deadline + travel_hours + 0.2 × verification_staff_minutes + response_delay_hours`. This is an administrative simulation score, not a clinical outcome or a probability. The planner's hard constraints and lexicographic ranking remain separate; the evidence engine and all benchmark policies use this same loss function. With no defensible public world weights, the fallback ranks by branch-change sensitivity per staff-minute, selects only a query that can change a branch before the deadline, and returns `net_value_estimate=null`.

**Clock contract:** `Incident.simulated_now` starts at the fixture's `as_of`; `IncidentSnapshot.as_of` copies it. Freshness, verification due times, travel deadlines, and approval checks use this simulated UTC clock, never the wall clock. `advance_simulated_clock(incident_id: UUID, minutes: int, actor: User) -> EvidenceRevision` advances monotonically for test/demo/evaluation and triggers replanning; a verification response advances by the fixture's declared response delay. Audit rows retain both real UTC `created_at` and `simulated_at` so event order and scenario time are distinguishable.

**Fixture coverage inputs:** Aster has 10 verified local units and 5 consumed units on each of the preceding 14 valid days, yielding two days of cover against a three-day administrative alert threshold. Its 40-box SupplyRequest is a *net transfer request* after its local stock, so the planner tests 40 rather than subtracting the 10 again. Fix Brook/Cedar demand rows, each source's observed UTC time, and route ETA in the machine-readable fixture. The Brook note remains raw text until Task 8; the early Task 3–4 Brook branch is provisional because its fresh count is unknown.

**Snapshot identity and loss:** `IncidentSnapshot.snapshot_id` is the stable string `"{incident_id}:{revision}"`; every decision-affecting mutation increments the revision, and PlanResult/PlanRun use that same snapshot ID. Count verification staff and delay terms in `LOSS_POLICY_V1` exactly once when comparing candidate questions or baselines. `EvidenceRevision` also represents a revision made solely by advancing the simulated clock; Task 12 defines the type.

## Review Focus

The five likely failure cases are pinned to the owning task's red test below:

1. Protected reserve also counted in `reserved`: Tasks 2 and 5 reject the fixture/import rather than subtracting twice.
2. Model cites a real span but changes its number or unit: Task 7 rejects that claim, leaving the source and receipt available.
3. A later report conflicts with a verified value: Task 9 retains both values, marks the field conflicted, and blocks ready status until human resolution.
4. Verification arrives after the deadline or is left unresolved: Task 12 records the response without granting verified stock or approval.
5. Two reviewers act on the same proposal/revision: Task 14 accepts at most one terminal decision and returns a stale-snapshot error for the other.

---

## P0 — executable evidence-to-review loop

**Public plausible worlds for P0 sensitivity:** Brook current on-hand is either 70 or 90; Cedar route is either closed or open. These are unweighted synthetic possibilities, not hidden truth or probabilities. Task 10 therefore uses the deterministic sensitivity mode unless explicitly documented public weights are later added.

**Remaining fixed fixture values:** Brook and Cedar each consume 2 boxes on each of the preceding 14 valid days. Aster/Brook/Cedar usable batch expiries are `2026-12-31`; signed current facts are observed at `2026-10-17T10:00:00Z`, the stale Brook count at `2026-10-15T09:00:00Z`, and Brook's unsigned note at `2026-10-17T11:00:00Z`. Brook's confirmed route arrives two hours after simulated dispatch; Cedar's route has unknown status until verified. Aster's request needs 40 boxes by `2026-10-18T12:00:00Z`. Use the same policy values in the blocked variant.

### Task 1: Bootstrap and lock the synthetic scenario

**Files:** Create `backend/manage.py`, `backend/pyproject.toml`, `backend/requirements.lock`, `backend/config/{__init__,settings,urls,wsgi}.py`, `backend/healthmesh/{__init__,apps,models}.py`, `backend/healthmesh/scenarios/{__init__,fixture}.py`, `backend/healthmesh/tests/test_fixture.py`, `fixtures/scenarios/storm-supply-01.json`, `fixtures/sources/brook-note.txt`, `.env.example`; modify `README.md`, `.gitignore`. `fixture.py` validates the one-product/three-facility source data; settings use SQLite locally and `DATABASE_URL` for PostgreSQL, never a committed secret. `requirements.lock` contains pinned dependencies matching `pyproject.toml`.

**Interfaces:** Produce `load_scenario(scenario_id: str) -> ScenarioSeed`; `ScenarioSeed` contains fixture revision, public facts, source references, administrative policy, and no hidden oracle truth.

- [ ] **Red:** Write `test_storm_fixture_is_complete_and_synthetic`: assert Aster need `40`, local stock `10`, 14 days at `5` units/day, Brook note `80`/`25` as raw text only, Cedar stock `55`, distinct `reserved`/`protected_reserve`, fixed UTC times/expiry/route, one base unit, and no patient/real identity fields. `test_bad_fixture_rejects_missing_reserve_accounting` must raise a validation error.
- [ ] **Run red:** After installing the pinned test runner needed for this bootstrap task, `cd backend; python -m pytest -q healthmesh/tests/test_fixture.py` → fixture import/validation failure; a missing `pytest` executable is not the expected red result.
- [ ] **Green:** Add the minimal Django project, fixture parser/schema, fixture files, test dependencies, `.env.example`, and README setup commands. Pin dependencies/lock in implementation; do not download or configure AMD here.
- [ ] **Verify:** Same pytest command → pass; `cd backend; python manage.py check` → no errors.
- [ ] **Commit boundary:** `chore: bootstrap synthetic HealthMesh scenario` (only Task 1 files).

### Task 2: Canonical inventory, provenance identities, and snapshots

**Files:** Create `backend/healthmesh/db_models/{__init__,core,sources}.py`, `backend/healthmesh/migrations/{__init__,0001_core}.py`, `backend/healthmesh/domain/{__init__,types,snapshot}.py`, `backend/healthmesh/scenarios/service.py`, `backend/healthmesh/tests/test_snapshot.py`; modify `backend/healthmesh/models.py`. `core.py` owns Incident, Facility, Product, StockBatch, SupplyRequest, CanonicalFact, and RouteObservation; `sources.py` owns immutable SourceDocument, SourceSpan, InventoryClaim. The fixture loader seeds signed/stale/manual facts with explicit basis, while candidate text claims remain unverified.

**Interfaces:** `create_incident(seed: ScenarioSeed, actor: User) -> Incident`; `build_snapshot(incident_id: UUID) -> IncidentSnapshot`. Stock identity and current canonical field state are separate from append-only InventoryClaim rows; each canonical field references its supporting claim/source and verification state. The scenario seeds only signed/manual facts; the Brook note remains raw text until Task 8, so Brook's fresh count is unknown/stale in Tasks 3–4. `SourceSpan` offsets are half-open Unicode character offsets in the stored decoded text.

- [ ] **Red:** `test_snapshot_preserves_old_and_new_claims` inserts a test-only unsigned 80 claim beside Brook's seeded old 95, then asserts both coexist and neither silently becomes fresh verified stock. `test_negative_or_double_counted_reserve_rejected` asserts negative quantities and `reserve_accounting != "separate"` fail. `test_rebuild_same_revision_is_identical` compares serialized snapshots.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_snapshot.py` → missing models/snapshot.
- [ ] **Green:** Add relational constraints, fixture seeding, UUIDs, UTC fields, source references, field statuses, and snapshot projection. Do not precreate forecast/evaluation/review models.
- [ ] **Verify:** Same pytest command → pass; `cd backend; python manage.py makemigrations --check --dry-run` → no model changes; `python manage.py check` → no errors.
- [ ] **Commit boundary:** `feat: add canonical inventory snapshots`.

In Task 2, the stored decoded text used for `SourceSpan` offsets is the sanitized AMD view. Keep the protected original blob under the same immutable document ID; Task 5 validates the redacted view's offsets before inference.

### Task 3: Pure deterministic Conditional Response Planner

**Files:** Create `backend/healthmesh/planner/{__init__,types,service}.py`, `backend/healthmesh/tests/test_planner.py`; modify `backend/healthmesh/domain/types.py`. No API or LLM code enters the planner.

**Interfaces:** `plan(snapshot: IncidentSnapshot) -> PlanResult`; `validate_proposal(snapshot: IncidentSnapshot, branch: PlanBranch) -> list[ConstraintCheck]`. Enumerate single-source and bounded two-source allocations; use the shared `PlanResult`/`PlanBranch` contract above. Rank by full fill, sender reserve preservation, on-time arrival, then travel/time cost, with stable tie breaking.

- [ ] **Red:** `test_fixture_yields_two_explicit_provisional_branches` asserts Brook and Cedar conditions and unknown reasons. `test_verified_brook_90_can_supply_40` asserts transferable `45` but allocation `40`. `test_two_sources_split_when_each_below_40` asserts a bounded combined allocation can satisfy 40 without violating either reserve. `test_expired_closed_route_or_unknown_mandatory_fact_never_ready` and a Hypothesis/property test assert no ready allocation exceeds verified stock or produces a negative balance; zero candidate facilities returns `NO_FEASIBLE_OPTION`.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_planner.py` → missing planner.
- [ ] **Green:** Implement enumeration and a separate constraint guard. Unknown expiry/storage/route/quantity stays provisional; definite failure gets a reason; product and unit must match. No model output influences feasibility.
- [ ] **Verify:** Same pytest command → pass; include deterministic repeated-run equality.
- [ ] **Commit boundary:** `feat: add deterministic conditional planner`.

### Task 4: First thin vertical demo — fixture to API to plan view

**Files:** Create `backend/healthmesh/db_models/planning.py`, `backend/healthmesh/migrations/0002_planning.py`, `backend/healthmesh/scenarios/api.py`, `backend/healthmesh/planner/api.py`, `backend/healthmesh/workflow/{__init__,auth}.py`, `backend/healthmesh/management/__init__.py`, `backend/healthmesh/management/commands/{__init__,seed_demo_users}.py`, `backend/healthmesh/tests/test_fixture_api.py`, `frontend/{package.json,package-lock.json,index.html,tsconfig.json,vite.config.ts}`, `frontend/src/{main.tsx,App.tsx,api.ts,types.ts,index.css}`, `frontend/src/features/{Login,PlanPreview}.tsx`, `frontend/src/features/PlanPreview.test.tsx`; modify `backend/healthmesh/models.py`, `backend/config/{settings,urls}.py`, `.env.example`, `README.md`, `.gitignore`. `planning.py` stores frozen PlanRun and branch/proposal records; the management command provisions synthetic users from environment passwords; this screen is deliberately small.

**Interfaces:** `POST /api/v1/scenarios`, `POST /api/v1/incidents/{id}/plan`, `GET /api/v1/incidents/{id}/plan-runs/{run_id}`; `POST/GET/DELETE /api/v1/session` use Django session auth and synthetic coordinator/reviewer accounts whose passwords come from environment, never the fixture. Frontend `createScenario(): Promise<ScenarioDto>` and `requestPlan(id: string): Promise<PlanDto>` mirror the PlanResult contract.

- [ ] **Red:** API test asserts scenario creation returns ID/seed, planner response includes revision, two branches, conditions, unknowns and reasons, and anonymous POST is denied. UI test asserts provisional label and disabled review control for both branches.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_fixture_api.py` and, after adding the minimal package/test-runner manifest and installing pinned frontend dependencies, `cd frontend; npm run test -- --run src/features/PlanPreview.test.tsx` → missing routes/UI, not a missing test runner.
- [ ] **Green:** Persist PlanRun from a frozen snapshot, add session login/CSRF and the smallest responsive scenario/branch UI. Document the synthetic-user provisioning command. Do not add all six final screens.
- [ ] **Verify:** Both focused tests pass; `cd frontend; npm run build` passes; manual local path can show fixture → conditional branches without AMD.
- [ ] **Commit boundary:** `feat: show fixture plan through API and web client`.

### Task 5: Immutable CSV and plain-text source ingestion

**Files:** Create `backend/healthmesh/sources/{__init__,service,parsers,spans,sanitizer,api}.py`, `backend/healthmesh/tests/test_sources.py`, `frontend/src/features/SourceUpload.tsx`, `frontend/src/features/SourceUpload.test.tsx`; modify `backend/config/{settings,urls}.py`, `frontend/src/{App.tsx,api.ts,types.ts}`, `.gitignore`. `parsers.py` owns strict CSV headers/units and UTF-8 text; `sanitizer.py` rejects known personal-data fields and creates the redacted AMD text view; `spans.py` issues line/CSV-row spans; `service.py` hashes and stores immutable synthetic documents.

**Interfaces:** `ingest_source(...) -> SourceDocument` as mapped above; `POST /api/v1/sources` with incident/facility plus CSV or `.txt`; returns source ID, SHA-256, kind, observed time, and duplicate indicator. Supported CSV must declare `base_unit=box` and `reserve_accounting=separate`. Valid CSV rows append `OBSERVED_UNVERIFIED` claims and advance the incident revision; a duplicate upload does neither.

- [ ] **Red:** `test_csv_round_trip_and_duplicate_hash` asserts exact parsed rows and same document ID for same scope/hash. `test_rejects_wrong_unit_negative_quantity_pii_header_and_oversize` also rejects `reserve_accounting != "separate"`, with 4xx and no new document. `test_text_spans_match_sanitized_offsets` asserts `amd_text[start:end] == quoted_text`; an accidental email is length-preserving redacted before AMD and a detected patient/MRN field stops processing. Upload UI test shows source ID.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_sources.py`; `cd frontend; npm run test -- --run src/features/SourceUpload.test.tsx` → fail.
- [ ] **Green:** Add type/size limits, synthetic-only field allowlist, deterministic same-length redaction for recognized direct identifiers in a separate AMD text view, source hashing, protected immutable original storage outside committed fixtures, and exact sanitized spans. Reject apparent authentic health data rather than transmitting it. Retain imports on later AMD failure.
- [ ] **Verify:** Focused tests pass; `python manage.py check` passes; duplicate upload leaves claim/document counts unchanged.
- [ ] **Commit boundary:** `feat: ingest synthetic CSV and text with source spans`.

### Task 6: AMD adapter contract and honest unavailable state

**Files:** Create `backend/healthmesh/ai_gateway/{__init__,adapter,types,service,api}.py`, `backend/healthmesh/db_models/ai.py`, `backend/healthmesh/migrations/0003_ai_receipts.py`, `backend/healthmesh/tests/test_amd_unavailable.py`; modify `backend/healthmesh/models.py`, `backend/config/{settings,urls}.py`, `.env.example`, `README.md`, `frontend/src/{api.ts,types.ts,App.tsx}`. The adapter is the only module allowed to know provider transport; `AIInferenceReceipt` stores sanitized metadata.

**Interfaces:** The three `AMDInferenceAdapter` methods in the contract map return typed outcomes `SUCCESS | UNAVAILABLE | INVALID_OUTPUT | TIMEOUT` with receipt where a call completed. `AIInferenceReceipt` holds provider, model ID, real UTC timestamp, latency, status, request ID if exposed, and input/output schema versions, without credentials or full prompts. `get_amd_adapter() -> AMDInferenceAdapter`; `GET /api/v1/amd/health` exposes availability without keys. Missing or unverified provider config returns `UNAVAILABLE`, not a mock success.

- [ ] **Red:** `test_missing_credentials_reports_unavailable` asserts health and extraction return `UNAVAILABLE`, no candidate claims, no fabricated request ID. `test_receipt_redacts_secret_and_prompt` asserts provider/model/timestamp/latency/status/input/output schema versions, optional genuine request ID, and no secret/full prompt fields; `test_timeout_does_not_advance_incident` keeps revision unchanged.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_amd_unavailable.py` → missing adapter.
- [ ] **Green:** Add abstract adapter, env checks (`AMD_API_KEY`, `AMD_API_BASE_URL`, `AMD_MODEL_ID`), bounded timeouts/retries/input sizes, receipt schema, and visible client unavailable status. Do not assume OpenAI compatibility or commit a guessed endpoint.
- [ ] **Verify:** Focused tests pass with AMD env unset; API health contains no secret; no successful receipt is synthesized.
- [ ] **Commit boundary:** `feat: define AMD adapter and unavailable behavior`.

### Task 7: Deterministic extraction schema and evidence-span validator

**Files:** Create `backend/healthmesh/ai_gateway/validation.py`, `backend/healthmesh/tests/test_extraction_validation.py`; modify `backend/healthmesh/ai_gateway/{types,service}.py`, `backend/healthmesh/sources/spans.py`. This is a pure validator, usable with a test stub before provider access.

**Interfaces:** `validate_extraction(raw: object, allowed_spans: Sequence[SourceSpan], base_unit: str) -> ValidatedClaims`; accepted claim fields are `on_hand`, `reserved`, `observed_at`, and uncertainty, with an issued span ID and exact numeric/unit support. Invalid claims go to quarantine with reason codes, never CanonicalFact.

- [ ] **Red:** Tests accept `80 box` and `25 box` from matching issued spans; reject malformed JSON, invented span ID, a genuine span saying `25` cited as `45`, mismatched unit, negative amount, and unsupported field. Assert accepted AI claims remain `OBSERVED_UNVERIFIED` despite model confidence.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_extraction_validation.py` → missing validator.
- [ ] **Green:** Validate syntax/types, issued IDs, offsets, quote/amount/unit, and allowlisted fields; treat uploaded instructions as data and never execute model-directed actions.
- [ ] **Verify:** Focused tests pass; rejected claims cannot appear in `build_snapshot()` as verified facts.
- [ ] **Commit boundary:** `feat: validate extracted claims against source spans`.

### Task 8: Real AMD free-text extraction and receipt

**Files:** Create `backend/healthmesh/ai_gateway/provider_event.py`, `backend/healthmesh/tests/test_amd_contract.py`, `backend/healthmesh/tests/test_amd_live.py`; modify `backend/healthmesh/ai_gateway/{adapter,service,api}.py`, `backend/config/urls.py`, `frontend/src/features/{SourceUpload.tsx,ExtractionReceipt.tsx}`, `frontend/src/{api.ts,types.ts}`, `README.md`. `provider_event.py` is filled in **only from the actual event-issued quickstart/protocol** observed during implementation.

**Interfaces:** `extract_source(source_id: UUID, actor: User) -> ExtractionResult`; `POST /api/v1/sources/{id}/extract` calls `AMDInferenceAdapter.extract_claims(text, allowed_spans, "v1")`, validates via Task 7, appends candidate claims, and returns receipt plus explicit accepted/rejected counts. Do not cache provider output in the MVP; each extraction gets a genuine new receipt, avoiding cross-document span reuse. Any later cache must key and revalidate by source hash, model/schema versions, and issued span scope.

- [ ] **Red:** Offline contract tests use a transport stub to assert the sanitized text/issued spans enter the adapter, validated claims stay unverified, changed note creates a new source/claims, timeout leaves imports intact, and prompt injection cannot mutate facts. Mark `test_event_endpoint_extracts_brook_note` with `amd_live`: assert a **real** configured call returns provider/model/timestamp/status/latency/schema versions, request ID if exposed, and span-backed `on_hand=80` plus `reserved=25` candidate claims.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_amd_contract.py` → route/service missing; with real configuration, `python -m pytest -q -m amd_live healthmesh/tests/test_amd_live.py` → fail until a genuine call works. A skip without configuration is **not** P0 acceptance.
- [ ] **Green:** First obtain and record the event's actual auth, endpoint path, request/response schema and model ID; implement that transport behind the adapter, persist sanitized receipts, and wire upload → extraction UI. Do not add a cache. If credentials/protocol are unavailable, keep Task 6's explicit unavailable behavior and this task pending.
- [ ] **Verify:** Offline tests pass; live test passes only after a real call; inspect receipt for no key/full prompt and compare returned span text. README states exactly what ran on AMD and how to reproduce it.
- [ ] **Commit boundary:** `feat: extract free-text claims with verified AMD transport` only after the real-path gate passes; otherwise commit only honest unavailable/contract work under its own message and leave this task unchecked.

### Task 9: Conflict detection and Evidence Workspace

**Files:** Create `backend/healthmesh/db_models/evidence.py`, `backend/healthmesh/migrations/0004_conflicts.py`, `backend/healthmesh/evidence/{__init__,conflicts,api}.py`, `backend/healthmesh/tests/test_conflicts.py`, `frontend/src/features/EvidenceWorkspace.tsx`, `frontend/src/features/EvidenceWorkspace.test.tsx`; modify `backend/healthmesh/models.py`, `backend/healthmesh/domain/snapshot.py`, `backend/config/urls.py`, `frontend/src/{App.tsx,api.ts,types.ts}`. Conflict detection groups claims by subject/field and retains every source value; snapshot projection overlays unresolved conflict status on any earlier signed value.

**Interfaces:** `detect_conflicts(incident_id: UUID) -> list[EvidenceConflict]`; `GET /api/v1/incidents/{id}/evidence` returns claims, spans, timestamps, conflicts, statuses, and receipt IDs. `EvidenceConflict` persists field key, claim IDs, type, state, and optional human resolution reference.

- [ ] **Red:** `test_old_95_new_80_remain_visible_and_conflicted` asserts both values/spans and no automatic latest-wins; with a constructed verified Brook count of 90, a 25→45 reserved note conflicts with signed 25 and blocks Brook ready status. UI test shows side-by-side source quote, timestamp, and unverified/conflicted label.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_conflicts.py`; `cd frontend; npm run test -- --run src/features/EvidenceWorkspace.test.tsx` → fail.
- [ ] **Green:** Add field-level detection, freshness classification, duplicate-equivalent handling, evidence API, and minimal evidence view. Do not resolve a conflict from a model explanation.
- [ ] **Verify:** Focused tests pass; a new conflicting claim does not overwrite or delete a prior claim.
- [ ] **Commit boundary:** `feat: expose conflicting evidence with provenance`.

### Task 10: Decision-Critical Evidence Engine

**Files:** Create `backend/healthmesh/db_models/verification.py`, `backend/healthmesh/migrations/0005_verification_tasks.py`, `backend/healthmesh/evidence/{types,ranking}.py`, `backend/healthmesh/tests/test_ranking.py`, `frontend/src/features/VerificationDesk.tsx`, `frontend/src/features/VerificationDesk.test.tsx`; modify `backend/healthmesh/models.py`, `backend/healthmesh/evidence/api.py`, `backend/config/urls.py`, `frontend/src/{App.tsx,api.ts,types.ts}`. `ranking.py` sees only the public snapshot and public synthetic priors, never evaluation truth.

**Interfaces:** `rank_verifications(snapshot, plan_result, budget) -> RankingResult` returns up to three ranked optional tasks with reason codes, evidence IDs, possible effects, effort/delay, `valuation_mode`, and nullable `net_value_estimate`. `POST /api/v1/incidents/{id}/verification-tasks` creates the selected task or returns `NO_POSITIVE_OPTIONAL_QUERY`; `must_verify_before_approval` reflects a separate mandatory gate, not query value.

- [ ] **Red:** A constructed Brook-sensitive case ranks stock confirmation above an irrelevant field; an invariant plan yields no optional task; a deadline-expired query is excluded; absent defensible priors uses `DETERMINISTIC_SENSITIVITY` and `net_value_estimate=null`. UI test shows 2–3 options, tradeoffs, and how answers change branches.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_ranking.py`; `cd frontend; npm run test -- --run src/features/VerificationDesk.test.tsx` → fail.
- [ ] **Green:** Implement bounded candidate enumeration, `LOSS_POLICY_V1` under explicit public-world weights, deterministic sensitivity fallback when weights are absent, and stable ranking. Reuse planner/guard for counterfactual outcomes; do not use LLM confidence as probability.
- [ ] **Verify:** Focused tests pass and no test reads `fixtures/evaluation/held_out.json` from ranking code.
- [ ] **Commit boundary:** `feat: rank decision-critical verification tasks`.

### Task 11: Grounded AMD conflict explanation and question draft

**Files:** Create `backend/healthmesh/ai_gateway/grounding.py`, `backend/healthmesh/tests/test_grounding.py`; modify `backend/healthmesh/ai_gateway/{provider_event,service,api,types}.py`, `frontend/src/features/{EvidenceWorkspace,VerificationDesk}.tsx`, `frontend/src/types.ts`, `README.md`. The selected field and evidence IDs are deterministic inputs; AMD may only phrase an explanation/question.

**Interfaces:** `explain_conflict(conflict_id, evidence) -> GroundedExplanation` must cite two existing spans; `draft_verification(task, evidence) -> SuggestedMessage` must mention only the selected task and cited spans. Extend `POST /api/v1/incidents/{id}/verification-tasks` response with optional grounded wording and its own inference receipt; unavailable/invalid wording falls back to labeled deterministic reason text.

- [ ] **Red:** Stub tests reject an invented span, unsupported numeric assertion, or a draft for an unselected field; unavailable calls preserve the deterministic selected task. `amd_live` contract tests require **real** successful explanation and draft receipts for the fixture conflict/task.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_grounding.py`; with configured AMD, `python -m pytest -q -m amd_live healthmesh/tests/test_amd_live.py` → the two added live tests fail until wired.
- [ ] **Green:** Implement the two provider operations using the verified Task 8 protocol, schema/grounding checks and sanitized receipts. Never let generated prose set ranking, status or canonical values.
- [ ] **Verify:** Offline and live focused tests pass for the final MVP; UI distinguishes generated wording from verified facts.
- [ ] **Commit boundary:** `feat: add grounded AMD evidence language` only with genuine live receipts; keep the live gate open if unavailable.

### Task 12: Human verification and explicit conflict resolution

**Files:** Create `backend/healthmesh/db_models/verification_response.py`, `backend/healthmesh/migrations/0006_verification_responses.py`, `backend/healthmesh/workflow/{clock,verification,api}.py`, `backend/healthmesh/tests/test_verification_response.py`; modify `backend/healthmesh/models.py`, `backend/healthmesh/domain/types.py`, `backend/healthmesh/evidence/conflicts.py`, `backend/config/urls.py`, `frontend/src/features/{EvidenceWorkspace,VerificationDesk}.tsx`, `frontend/src/{api.ts,types.ts}`. A human response is append-only; any corrected canonical fact points to the human evidence revision. `clock.py` owns the simulated-time interface in the contract map.

**Interfaces:** `respond_to_verification(task_id, answer, actor) -> EvidenceRevision`; `POST /api/v1/verification-tasks/{id}/respond`, `POST /api/v1/claims/{id}/review`, `POST /api/v1/conflicts/{id}/resolve`. `VerificationAnswer` has `CONFIRMED | REJECTED | UNRESOLVED`, value/unit if confirmed, claimed source, and expected incident revision. Response delay advances `Incident.simulated_now`; late responses cannot verify a fact for the expired decision.

- [ ] **Red:** A coordinator confirms Brook 90 from a named simulated recount, generating a new `HUMAN_VERIFIED` claim and revision while old 95/80 remain; unresolved/late answers create no verified fact. Advancing simulated time past the deadline changes freshness/approval while wall-clock delay alone does not; time cannot move backward. Wrong role, missing source, mismatched unit, and stale expected revision return structured 4xx errors.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_verification_response.py` → missing API/service.
- [ ] **Green:** Validate actor/source/units/deadline using the simulated clock, append response and claim, resolve only the selected conflict, atomically advance incident revision and simulated time, and preserve all earlier evidence. Implement minimal response form.
- [ ] **Verify:** Focused tests pass; exact old claim IDs and source spans remain queryable.
- [ ] **Commit boundary:** `feat: record human evidence revisions`.

### Task 13: Automatic replanning on every evidence revision

**Files:** Create `backend/healthmesh/workflow/replan.py`, `backend/healthmesh/tests/test_replanning.py`; modify `backend/healthmesh/{ai_gateway/service.py,evidence/conflicts.py,workflow/verification.py,planner/api.py}`, `frontend/src/features/{PlanPreview,VerificationDesk}.tsx`, `frontend/src/{api.ts,types.ts}`. The service archives prior PlanRun rows and persists a new run synchronously after accepted evidence changes.

**Interfaces:** `replan_after_evidence_change(incident_id, expected_prior_revision) -> PlanRun` requires current incident revision to equal `expected_prior_revision + 1`; replay for that revision returns the existing run. Mutating evidence APIs return `new_revision` and `new_plan_run_id`. PlanRuns retain the snapshot ID/revision and never change their stored branches.

- [ ] **Red:** `test_confirmed_90_replans_brook_to_ready` compares old provisional run with new ready run and unchanged old JSON. `test_reserved_45_conflict_replans_to_provisional` and `test_unresolved_answer_does_not_promote_branch` cover the AMD note variant and no-op answer; duplicate callback cannot create divergent revisions.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_replanning.py` → old plan remains current.
- [ ] **Green:** Centralize revision-triggered planning and call it from CSV import, extraction, human claim review, conflict resolution, verification response, and simulated-clock advancement after transactionally accepted changes. Refresh client plan from returned run ID.
- [ ] **Verify:** Focused tests pass; fixture → extract → answer visibly changes branch without a manual plan button.
- [ ] **Commit boundary:** `feat: replan from revised evidence snapshots`.

### Task 14: Simulation-only approval and rejection guard

**Files:** Create `backend/healthmesh/db_models/review.py`, `backend/healthmesh/migrations/0007_review_decisions.py`, `backend/healthmesh/workflow/review.py`, `backend/healthmesh/tests/test_review.py`, `frontend/src/features/ReviewControls.tsx`, `frontend/src/features/ReviewControls.test.tsx`; modify `backend/healthmesh/models.py`, `backend/healthmesh/workflow/api.py`, `backend/config/urls.py`, `frontend/src/features/PlanPreview.tsx`, `frontend/src/{api.ts,types.ts}`. ReviewDecision records actor, rationale, time and action; no fulfillment integration exists.

**Interfaces:** `review_proposal(proposal_id, decision, expected_revision, actor) -> ReviewDecision`; `POST /api/v1/proposals/{id}/review`. Approval **and rejection** require the synthetic reviewer role; the coordinator completes import/verification then hands the case to that reviewer. Approval also requires fresh Incident/PlanRun snapshot, current signed-off mandatory facts, request/unit/expiry/storage/route/freshness/reserve checks, and a ready branch; rejection records rationale without dispatch.

- [ ] **Red:** Tests deny provisional/unknown-route/expired/oversize proposals, stale revision, anonymous or coordinator approval/rejection. A concurrent two-review test asserts exactly one terminal decision and second `STALE_SNAPSHOT`; reviewer reject path records actor/rationale. UI test enables approve only for `READY_FOR_REVIEW` and labels it simulated.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_review.py`; `cd frontend; npm run test -- --run src/features/ReviewControls.test.tsx` → fail.
- [ ] **Green:** Lock incident/proposal in a DB transaction, reproject a fresh snapshot, rerun the independent guard, check optimistic version, and persist a terminal simulated decision. Do not update physical inventory or call an external service.
- [ ] **Verify:** Focused tests pass on SQLite; repeat concurrency check on PostgreSQL when available because lock behavior differs.
- [ ] **Commit boundary:** `feat: gate simulated review on fresh verified facts`.

### Task 15: Append-only audit, state history, and receipt view

**Files:** Create `backend/healthmesh/db_models/audit.py`, `backend/healthmesh/migrations/0008_audit.py`, `backend/healthmesh/workflow/audit.py`, `backend/healthmesh/tests/test_audit.py`, `frontend/src/features/AuditTimeline.tsx`, `frontend/src/features/AuditTimeline.test.tsx`; modify `backend/healthmesh/models.py`, `backend/healthmesh/{scenarios/service.py,sources/service.py,ai_gateway/service.py,evidence/conflicts.py,workflow/verification.py,workflow/replan.py,workflow/review.py,workflow/api.py}`, `backend/config/urls.py`, `frontend/src/{App.tsx,api.ts,types.ts}`. AuditEvent is append-only with actor/action/entity/before/after/time/request ID; sensitive content stays out.

**Interfaces:** `append_audit_event(actor, action, entity_ref, before_ref, after_ref, request_id) -> AuditEvent`; `GET /api/v1/incidents/{id}/audit` returns ordered immutable events and sanitized inference receipt references. `Incident.state` follows the specified state machine, including blocked and simulated terminal states.

- [ ] **Red:** Full fixture interaction yields ordered import, extraction, conflict, verification, replan and review events; attempted update/delete of an existing AuditEvent is rejected; timeout shows `AI_UNAVAILABLE` without `EXTRACTION_SUCCEEDED`; UI renders revision links and sanitized receipt.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_audit.py`; `cd frontend; npm run test -- --run src/features/AuditTimeline.test.tsx` → fail.
- [ ] **Green:** Prevent AuditEvent update/delete through model, QuerySet, admin and API paths; transactionally emit events from all earlier mutations, expose audit API and timeline, and enforce state transitions. In P0, allow `EVIDENCE_REVIEW → PLAN_PROVISIONAL` while coverage is deferred; Task 17 enables the full `RISK_ASSESSED` transition. Do not copy source body or credentials into event payloads.
- [ ] **Verify:** Focused tests pass; historical PlanRun and claim IDs still resolve after newer revisions.
- [ ] **Commit boundary:** `feat: expose append-only simulated decision history`.

### Task 16: P0 browser acceptance for happy and refusal paths

**Files:** Create `frontend/playwright.config.ts`, `frontend/e2e/healthmesh-happy.spec.ts`, `frontend/e2e/healthmesh-blocked.spec.ts`, `backend/healthmesh/tests/test_p0_api_journey.py`; modify `frontend/package.json`, `frontend/src/App.tsx`, `README.md`. These tests close the vertical demo, not a new feature layer.

**Interfaces:** The browser uses the existing scenario, upload, extraction, evidence, verification, review and audit APIs. A test-only AMD transport stub can exercise UI deterministically, but the separate Task 8/11 `amd_live` gates still require real calls.

- [ ] **Red:** Happy API/browser flow asserts source → AMD candidate/receipt → conflict → provisional branches → selected task → human Brook 90 → changed ready branch → coordinator signs out/reviewer signs in → simulated approval → audit. Blocked flow asserts Brook 70 plus Cedar route closed yields `NO_FEASIBLE_OPTION`/blocked, no enabled approval and visible reasons. Include the changed-note 25→45 case.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_p0_api_journey.py`; `cd frontend; npx playwright test e2e/healthmesh-happy.spec.ts e2e/healthmesh-blocked.spec.ts` → fail until connected.
- [ ] **Green:** Wire any missing page transitions, stable test fixtures, and accessible state labels. Keep Playwright's stub clearly test-only, never a public AMD-success indicator.
- [ ] **Verify:** Focused API and Playwright tests pass, `cd frontend; npm run build` passes, and Task 8/11 live tests pass before declaring **P0 complete**. Manually run the browser flow against the configured real AMD endpoint and record its genuine receipt ID. If AMD is unavailable, report P0 as live-gate blocked despite offline green tests.
- [ ] **Commit boundary:** `test: prove synthetic evidence-to-review demo`.

## P1 — transparent coverage and fair comparison

**Coverage accounting policy:** For local-serving coverage use verified usable `on_hand - reserved` so protected reserve remains available to the sending facility's own patients; for transferability subtract protected reserve as well. Show both quantities separately. This is a synthetic administrative policy fixed before forecast tests.

### Task 17: Consumption coverage and shortage view

**Files:** Create `backend/healthmesh/db_models/forecast.py`, `backend/healthmesh/migrations/0009_forecast.py`, `backend/healthmesh/forecast/{__init__,service,api}.py`, `backend/healthmesh/tests/test_forecast.py`, `frontend/src/features/CoverageView.tsx`, `frontend/src/features/CoverageView.test.tsx`; modify `backend/healthmesh/models.py`, `backend/config/urls.py`, `frontend/src/{App.tsx,api.ts,types.ts}`. Store DemandObservation and immutable ForecastRun with model version, snapshot, inputs, assumptions and limitations.

**Interfaces:** `calculate_coverage(snapshot, valid_days=14) -> CoverageResult`; `POST /api/v1/incidents/{id}/assess` returns valid-day mean, verified usable local stock, days of cover, administrative threshold, and insufficiency reasons. Fixture policy states whether protected reserve counts toward local-serving stock (choose and document this before tests; transfer accounting remains separate).

- [ ] **Red:** Test 14-day reference mean/cover, zero stock, zero/no demand, stale input and outlier policy; assert missing/inconsistent history returns `INSUFFICIENT_DEMAND_HISTORY` and never an invented number. UI test exposes calculation inputs/provenance and labels alert as synthetic administrative policy.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_forecast.py`; `cd frontend; npm run test -- --run src/features/CoverageView.test.tsx` → fail.
- [ ] **Green:** Add deterministic calculation: include nonnegative days marked `VALID`; exclude only days explicitly flagged `OUTLIER_CONFIRMED` with a recorded reason, and show unreviewed spikes as limitations rather than silently trimming them. Zero valid demand yields a null cover with `ZERO_DEMAND`. Add frozen ForecastRun, shortage badge and three-node network detail without opaque AI risk scores.
- [ ] **Verify:** Focused tests pass; repeated fixed snapshot returns identical numbers.
- [ ] **Commit boundary:** `feat: show transparent synthetic stock coverage`.

### Task 18: Independent seeded evaluation and baseline policies

**Files:** Create `fixtures/evaluation/{development,held_out}.json`, `backend/healthmesh/db_models/evaluation.py`, `backend/healthmesh/migrations/0010_evaluation.py`, `backend/healthmesh/evaluation/{__init__,scenario,policies,metrics,service,api}.py`, `backend/healthmesh/tests/test_evaluation.py`; modify `backend/healthmesh/models.py`, `backend/config/urls.py`, `README.md`. The evaluator owns hidden truth; no import from it is allowed in `planner`, `evidence`, or `forecast`.

**Interfaces:** `run_evaluation(seed: int, policies: Sequence[PolicyName]) -> SimulationRun`; `POST /api/v1/evaluations/run`, `GET /api/v1/evaluations/{id}`. Policies: `LATEST_REPORT`, `VERIFY_ALL`, `OLDEST_FIRST`, `SENSITIVITY_HEURISTIC`, `HEALTHMESH`; `ORACLE` is labeled upper bound only. All use identical deadlines, budgets, truth cases, hard guards and synthetic loss function.

- [ ] **Red:** Fixed seed produces byte-equivalent metrics; hidden truth cannot be loaded by planning modules; all policies see identical scenario IDs/constraints/budget; injected bad ready proposal increases infeasibility; favorable and unfavorable cases remain in results. Assert metrics for unmet units, verification count/minutes/delay, regret, abstention, citation validity, and AMD latency/failure when present.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_evaluation.py` → missing evaluator.
- [ ] **Green:** Build locked held-out generator/fixtures, independent truth scoring using `LOSS_POLICY_V1`, baseline policies, extraction field precision/recall on labeled synthetic text, distribution and failure-case artifacts. Never tune on held-out truth or compare against external products without data.
- [ ] **Verify:** Focused tests pass; run two identical seeds and compare saved metrics; README discloses scenario count and methodology, including losses or no gain versus sensitivity heuristic.
- [ ] **Commit boundary:** `feat: add reproducible policy evaluation`.

### Task 19: Benchmark/results screen and final P1 acceptance

**Files:** Create `frontend/src/features/BenchmarkView.tsx`, `frontend/src/features/BenchmarkView.test.tsx`, `frontend/e2e/benchmark.spec.ts`; modify `frontend/src/{App.tsx,api.ts,types.ts}`, `README.md`. This view pairs actual simulation metrics with method/limits and links to failed cases.

**Interfaces:** `getEvaluation(id: string): Promise<EvaluationDto>` consumes Task 18's result; render baseline names, same-seed/budget note, distributions, extraction/citation performance, AMD receipt stats, and explicit ORACLE upper-bound label.

- [ ] **Red:** UI test asserts a losing HEALTHMESH case remains visible, ORACLE is excluded from fair competitor claims, unknown metrics show `not measured`, and no clinical-outcome wording appears. Browser test navigates from incident to evaluation and a failure case.
- [ ] **Run red:** `cd frontend; npm run test -- --run src/features/BenchmarkView.test.tsx`; `npx playwright test e2e/benchmark.spec.ts` → fail.
- [ ] **Green:** Add results screen, scenario/method disclosure and failure links; update README with real measured output only after running the harness.
- [ ] **Verify:** Focused tests and `npm run build` pass; run the locked evaluation once and compare UI values to API values. Complete P1 acceptance only when Task 17/18 outputs are reproducible.
- [ ] **Commit boundary:** `feat: publish synthetic benchmark results in UI`.

### Task 20: Public-demo configuration and operational recovery

**Files:** Create `backend/healthmesh/tests/test_deployment_controls.py`, `backend/healthmesh/workflow/throttling.py`, `backend/healthmesh/management/commands/purge_demo_sources.py`, `docs/deployment.md`; modify `backend/config/settings.py`, `backend/pyproject.toml`, `backend/requirements.lock`, `backend/healthmesh/{sources/service.py,ai_gateway/service.py}`, `.env.example`, `README.md`, `frontend/src/App.tsx`. Production settings must require PostgreSQL/HTTPS host configuration; local SQLite remains explicit.

**Interfaces:** Environment contract is `DATABASE_URL`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `AMD_API_KEY`, `AMD_API_BASE_URL`, `AMD_MODEL_ID`, `DEMO_MODE`, `DEMO_COORDINATOR_PASSWORD`, `DEMO_REVIEWER_PASSWORD`, `MAX_UPLOAD_MB`, `SOURCE_RETENTION_DAYS`; deployment health distinguishes API readiness from AMD availability.

- [ ] **Red:** Tests reject missing production secret/host, disallowed CORS, oversized upload, unauthenticated writes, excessive AMD calls, and unsafe file types; timeout/retry preserves imports and failed extraction never advances review state. Assert logs exclude credentials/full source and include request/scenario IDs.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_deployment_controls.py` → failing controls.
- [ ] **Green:** Add CORS/CSRF/session policy, role-aware rate limits, bounded AMD retries, source retention command/policy, protected upload storage, structured sanitized logging and documented PostgreSQL/HTTPS/static-host deployment steps. Do not claim a hosted URL until one exists.
- [ ] **Verify:** Focused tests, `python manage.py check --deploy` under production-like env, full backend suite, frontend build and both browser paths pass. As a separate deployment checkpoint, an authorized operator chooses an available host, configures secrets/PostgreSQL/HTTPS, runs migrations, opens the public frontend/API URL, completes the real AMD browser flow, and records that URL and genuine receipt in `docs/deployment.md`. If this has not happened, Task 20 and submission readiness remain open.
- [ ] **Commit boundary:** `chore: harden synthetic public demo deployment`.

## P2 — only after P0 and P1 acceptance

### Task 21: Text-extractable PDF source ingestion

**Files:** Create `backend/healthmesh/sources/pdf_text.py`, `backend/healthmesh/tests/test_pdf_text.py`, `fixtures/sources/brook-note-text.pdf`; modify `backend/pyproject.toml`, `backend/requirements.lock`, `backend/healthmesh/sources/{parsers,spans,service}.py`, `frontend/src/features/SourceUpload.tsx`, `README.md`. Use pypdf for extractable text only; no OCR.

**Interfaces:** `extract_pdf_spans(content: bytes) -> list[SourceSpanDraft]` preserves page number and text offsets; `ingest_source(...)` accepts `.pdf` only when text and citations can be verified.

- [ ] **Red:** Test page/offset quote round-trip, empty/image-only PDF refusal, encrypted/oversize rejection, and unchanged text/CSV behavior.
- [ ] **Run red:** `cd backend; python -m pytest -q healthmesh/tests/test_pdf_text.py` → unsupported PDF.
- [ ] **Green:** Add bounded extraction, page-aware spans and explicit unsupported-file errors, then connect existing AMD validation path.
- [ ] **Verify:** Focused and full ingestion/AMD contract tests pass; live AMD PDF-derived text is still a real call or visibly unavailable.
- [ ] **Commit boundary:** `feat: ingest text-extractable synthetic PDFs`.

### Task 22: Responsive network and diagram polish

**Files:** Create `frontend/src/features/NetworkView.tsx`, `frontend/src/features/NetworkView.test.tsx`; modify `frontend/src/{App.tsx,index.css}`, `frontend/src/features/{PlanPreview,CoverageView,EvidenceWorkspace,VerificationDesk}.tsx`, `frontend/e2e/healthmesh-happy.spec.ts`. This is visual presentation of existing facts, not new decision logic.

**Interfaces:** `NetworkView` consumes existing `PlanDto` and `CoverageResultDto`; every node/edge links to its evidence/constraint detail and preserves verified/provisional/blocked distinctions.

- [ ] **Red:** Component tests assert source freshness, reserve implication, route state and blocked reason text; mobile browser smoke asserts all review and source details remain reachable by keyboard/touch and no overflow obscures state.
- [ ] **Run red:** `cd frontend; npm run test -- --run src/features/NetworkView.test.tsx`; `npx playwright test e2e/healthmesh-happy.spec.ts` → missing view/mobile assertion.
- [ ] **Green:** Add three-node network, responsive layout, accessible focus/status styles, and explanatory detail links. No extra SKU, logistics solver, or clinical terminology.
- [ ] **Verify:** Focused tests, `npm run build`, and happy/blocked Playwright paths pass at desktop and mobile widths.
- [ ] **Commit boundary:** `feat: clarify synthetic network and plan views`.

## Acceptance, Coverage, and Handoff

**Early vertical checkpoints:** Task 4 gives fixture → snapshot → deterministic API plan → UI. Task 8 adds genuine AMD note → validated unverified claims and receipt. Tasks 9–16 complete conflict → ranked question → human response → changed plan → simulated review → audit, including refusal. If live AMD access blocks Task 8 or 11, continue downstream offline implementation with a clearly labeled test transport while those live checks remain open; P0 is not complete until the real calls pass. P1 can be built offline afterward, while P2 still waits for completed P0 and P1 acceptance. P1 completes the architecture's full judge-facing success definition; P2 is conditional polish.

**Specification coverage:** §§1–5 map to Tasks 1, 4–5, 8–16; §6 to the contract map and Tasks 1–4; §7 to Tasks 2, 4, 6, 9–10, 12–15, 17–18; §8 to Tasks 6–8 and 11; §9 to Task 17; §10 to Task 10; §11 to Tasks 3, 13–14; §12 and §18 to Tasks 5–7, 12–16, 20; §13 to Tasks 4–5, 8–10, 12–15, 17–18; §14 to Tasks 4, 9–10, 12–15, 17, 19, 22; §§15–16 to Tasks 1, 3, 8, 16–19; §17 to Tasks 6, 8, 14, 20; §§19–21 to the checkpoints, README and measured-results tasks. Submission video, slide deck, cover image and actual event deadline are external follow-up deliverables, not application code; verify the signed-in portal before a submission claim.

**Resolved design tensions:** The user's explicit ordering places PDF in P2 despite §4's broader MVP ingestion row. Forecast and benchmark are P1 but are required before calling the complete judge-facing demo accepted. All three §8 AMD operations are in P0; only a real configured inference satisfies the live gate. Optional question value never waives mandatory signoff. Human claim review/conflict resolution routes are added because the UI requires them and §13 omits them. Fixture reserve accounting and public priors are locked before evaluation. No P3/post-hackathon features are scheduled.

**Known blockers and honest claims:** The repo has no AMD credentials, verified endpoint protocol/model, dependency manifests, deployment host, or checked-in fixture yet. The implementation team must use the actual event quickstart and a real test call. If unavailable, extraction/grounded live tests remain open and the UI shows AMD unavailable; offline stubs are test instruments only. PostgreSQL concurrency and hosted-demo security need validation on the actual target environment. The untracked architecture document must be included deliberately if the team wants the plan's spec link to travel with the repository.

**Plan self-review:** Each interface used later is defined above or by an earlier task; `IncidentSnapshot.revision` flows through PlanRun and review; generated language never feeds planner or verification ranking; hidden oracle data is isolated; each task has a red test, focused command, green step, verification, and commit boundary. At execution, follow task order, allowing the stated offline continuation while AMD live gates remain open, and re-run focused tests after each commit. The recommended execution method is **subagent-driven development** with fresh implementation and review passes per task, because source provenance, approval concurrency, and the real AMD boundary deserve independent gates. Review this plan and choose that method or native execution before implementation.
