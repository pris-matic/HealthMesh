# HealthMesh

HealthMesh is a synthetic administrative supply simulation for one fictional
product and three fictional facilities. It does not perform clinical decisions
or real transfers. The current bootstrap provides a validated public scenario
and a Django/DRF backend; workflow and AMD integration arrive in later tasks.

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
../.venv/Scripts/python.exe manage.py runserver
```

`.env.example` documents supported settings. Export them in your shell or use
your process manager; this bootstrap does not automatically load `.env` files.
Without `DATABASE_URL`, Django uses `backend/db.sqlite3`. For PostgreSQL, export
`DATABASE_URL` with your own database credentials before starting Django. No
credentials belong in the scenario or repository. A missing local secret uses
an ephemeral random key, so sessions will expire on process restart. Public
deployment configuration is outside this bootstrap.

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

No AMD model, credentials, endpoint, or successful inference is configured by
this task. Passing these bootstrap tests does not satisfy the live AMD gate.
