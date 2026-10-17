# HealthMesh

HealthMesh is a synthetic administrative supply simulation for one fictional
product and three fictional facilities. It does not perform clinical decisions
or real transfers. The current bootstrap provides a validated public scenario
and an authenticated fixture-to-plan web preview; review workflow and AMD
integration arrive in later tasks.

## Local setup (PowerShell)

Install Python 3.12 and `uv`, then run from the repository root:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location).Path '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location).Path '.python'
uv venv --python 3.12 .venv
uv pip sync --python .venv/Scripts/python.exe backend/requirements.lock
# A persistent random secret is required when using sessions across restarts.
$env:DJANGO_SECRET_KEY = & .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(50))"
$env:DJANGO_DEBUG = 'true'
Set-Location backend
../.venv/Scripts/python.exe manage.py check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe manage.py migrate
# Supply your own synthetic account passwords in this shell.
$env:DEMO_COORDINATOR_PASSWORD = 'replace-with-your-local-password'
$env:DEMO_REVIEWER_PASSWORD = 'replace-with-another-local-password'
../.venv/Scripts/python.exe manage.py seed_demo_users
../.venv/Scripts/python.exe manage.py runserver
```

`.env.example` documents supported settings. Export them in your shell or use
your process manager; this bootstrap does not automatically load `.env` files.
Without `DATABASE_URL`, Django uses `backend/db.sqlite3`. For PostgreSQL, export
`DATABASE_URL` with your own database credentials before starting Django. No
credentials belong in the scenario or repository. A missing local secret uses
an ephemeral random key, so sessions will expire on process restart. Public
deployment configuration is outside this bootstrap.

In a second PowerShell window, run `cd frontend`, `npm.cmd ci`, then
`npm.cmd run dev`. Open the printed localhost URL, sign in as
`demo-coordinator` with your chosen password, and select **Load scenario and
plan**. Both conditional branches are provisional; review is disabled. The
fixture runs without AMD. Times display explicitly in Asia/Manila; storage
uses UTC. `demo-reviewer` can read authenticated plan URLs but cannot create
scenarios or run plans. Provisioning again resets these two users' passwords
and demo roles. Do not use those usernames for real accounts.

Vite proxies `/api` to Django on `127.0.0.1:8000`, keeping session cookies and
CSRF same-origin. Session `GET` bootstraps a CSRF token; `POST` signs in and
rotates it; `DELETE` signs out. Every mutation requires CSRF, and scenario/plan
creation requires the coordinator role. The plan API persists a frozen input
snapshot and result, plus one UUID proposal per atomic branch (all its legs
together). `GET /api/v1/incidents/{id}/plan-runs/{run_id}` retrieves that saved
run even after evidence changes. It never dispatches a transfer.

Frontend verification: `npm.cmd run test -- --run` and `npm.cmd run build`.

Dependencies, including the test extra and transitive packages, are pinned in
`backend/requirements.lock`. To deliberately refresh the lock from the root:

```powershell
uv pip compile backend/pyproject.toml --extra test --python .venv/Scripts/python.exe --output-file backend/requirements.lock
```

## Locked public scenario

`fixtures/scenarios/storm-supply-01.json` is revision 1 of the synthetic scenario.
Load it with `healthmesh.scenarios.fixture.load_scenario('storm-supply-01')` from
the backend. It returns a typed `ScenarioSeed` containing public facts, signed
fixture source references, a hashed raw note, and administrative policy. Invalid
or unknown fields and missing reserve accounting fail validation. Internal
identities are UUIDs and timestamps are timezone-aware UTC.

At simulated `2026-10-17T12:00:00Z`, Aster requests a net transfer of 40 boxes by
the following noon. Its 10 local boxes are already accounted for in that net
request. Demand is 5 boxes per day for the preceding 14 days; Brook and Cedar
each consume 2 per day. The administrative coverage alert threshold is 3 days.
Local coverage uses usable `on_hand - reserved`, including protected reserve;
transfer availability also subtracts `protected_reserve`, which is explicitly
separate from `reserved`.

Brook's signed count of 95 is stale. Its separately signed 25 reserved and
20 protected boxes remain public facts. The unsigned note says 80 on hand and
25 reserved, and remains raw text until actual AMD extraction; loading it
creates no candidate or canonical claims. Its SHA-256 is checked on every load.
Brook's route is open with a 2-hour ETA. Cedar has 55 boxes, 0 reserved and
10 protected; its route is unknown, with a 3-hour ETA conditional on verified
open status. Unknown route status must never imply usable transport.

The fixture fixes a 24-hour freshness limit, a 10-staff-minute/one-question
budget, Brook count verification at 3 staff minutes/10 simulated minutes delay,
and Cedar route verification at 5 staff minutes/20 simulated minutes delay.
Public possibilities (Brook 70 or 90; Cedar closed or open) are unweighted
synthetic alternatives, with no selected true outcome. The shared synthetic
loss policy is `100 * unmet_units_by_deadline + travel_hours +
0.2 * verification_staff_minutes + response_delay_hours`; it is not a clinical
score. Hidden evaluation truth does not belong in public fixtures.

## AMD availability

Signed-in coordinators and reviewers can read `GET /api/v1/amd/health`. It
returns `UNAVAILABLE` with `missing_configuration` when any of `AMD_API_KEY`,
`AMD_API_BASE_URL`, or `AMD_MODEL_ID` is absent; populated settings still return
`provider_transport_unverified`. The client displays this state and keeps the
synthetic fixture planner usable. No credential, endpoint, or model setting is
included in the health response.

The [hackathon FAQ](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii)
identifies [AMD Token Factory](https://notebooks.amd.com/tokenfactory), whose
public page requires AMD ADP sign-in. No authenticated event quickstart, enabled
model, verified base URL, credential, or successful call is available here.
No endpoint or OpenAI-compatible protocol is assumed. Real transport and the
live acceptance gate remain Task 8 work.

`AMDInferenceAdapter` defines extraction, conflict explanation, and verification
wording outcomes: `SUCCESS`, `UNAVAILABLE`, `INVALID_OUTPUT`, and `TIMEOUT`.
The current adapter makes zero outbound calls and creates no inference receipts.
Its contract bounds future transport to 10 seconds and zero retries, extraction
input to 64 KiB of UTF-8 text and 128 issued spans, and output wording to 4000
characters. Task 8 must enforce network deadlines with the verified transport.
The extraction service sanitizes text and checks span offsets against that view
before entering the adapter; it never reads original upload blobs. Candidate
claims require the later provenance validator and remain unverified evidence.

Completed-call receipts allow only provider, model ID, real UTC timestamp,
latency, completed status, optional genuine provider request ID, and input/output
schema versions. They store no full prompt, response, credentials, or headers.
Unavailable calls and timeouts create neither receipts nor candidate evidence
and do not advance an incident revision. Passing these contract tests does not
satisfy the live AMD gate.
