# PRFactory

PRFactory is a Slack- and UI-driven feature orchestration system that turns requests into validated specs, runs build jobs, and publishes PR/preview outputs with approval gates.

This repository is local-first (Docker Compose) and production-aware (Modal/edge auth support), with mock mode available for fast onboarding.

## Core workflow

1. Intake from Slack or web UI.
2. Spec validation (`READY_FOR_BUILD` or `NEEDS_INFO`).
3. Background build execution (RQ worker).
4. PR and preview updates.
5. Product/reviewer approval and merge-gated progression.

State machine:

`NEW -> NEEDS_INFO -> READY_FOR_BUILD -> BUILDING -> PR_OPENED -> PREVIEW_READY -> PRODUCT_APPROVED -> READY_TO_MERGE -> MERGED`

Failure states:

`FAILED_SPEC`, `FAILED_BUILD`, `FAILED_PREVIEW`, `NEEDS_HUMAN`

## Architecture

- `api` (FastAPI): UI pages, REST API, health/runtime endpoints, optional Slack HTTP events.
- `worker` (RQ): queued build execution and PR/preview orchestration.
- `cleanup` worker: workspace retention cleanup + stale callback alerts.
- `slackbot` (optional profile): Slack Socket Mode bot process.
- `db` (Postgres) and `redis`.

External integrations are adapter-based (`SlackAdapter`, `GitHubAdapter`, `CodeRunnerAdapter`) for mock/real runtime switching.

## Quick start (local)

### 1) Prerequisites

- Docker Desktop
- Git
- VS Code (recommended)

### 2) Configure environment

```powershell
Copy-Item .env.example .env
```

Defaults are local-friendly (`MOCK_MODE=true`, `AUTH_MODE=disabled`).

### 3) Start services

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Or helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
```

With Slack profile:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack
```

### 4) Open the app

- `http://localhost:8000`
- `http://localhost:8000/health`
- `http://localhost:8000/health/ready`
- `http://localhost:8000/health/runtime`

### 5) Run smoke test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke_test.ps1
```

### 6) Optional migrations path test

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1
```

Legacy DB bootstrap stamp:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1 -BootstrapStamp
```

## Runtime modes

- `MOCK_MODE=true`
  - No real GitHub/Slack credentials required.
  - PR/preview outputs are simulated.
  - Best for local workflow validation.

- `MOCK_MODE=false`
  - Requires real GitHub integration and execution path.
  - Supports signed callback ingestion via `/api/integrations/execution-callback`.

## Auth and RBAC

`AUTH_MODE`:

- `disabled` (default local)
- `api_token` (requires `X-FF-Token`)
- `edge_sso` (trusted identity headers)

RBAC is configured through:

- `RBAC_REQUESTERS`
- `RBAC_BUILDERS`
- `RBAC_APPROVERS`

Production guardrails are enforced when `APP_ENV=prod` and `ENFORCE_PRODUCTION_SECURITY=true`.

## API surface (key endpoints)

Health:

- `GET /health`
- `GET /health/ready`
- `GET /health/metrics`
- `GET /health/runtime`

Feature orchestration:

- `GET /api/feature-requests`
- `POST /api/feature-requests`
- `GET /api/feature-requests/{feature_id}`
- `POST /api/feature-requests/{feature_id}/revalidate`
- `PATCH /api/feature-requests/{feature_id}/spec`
- `POST /api/feature-requests/{feature_id}/build`
- `POST /api/feature-requests/{feature_id}/approve`

Integrations:

- `POST /api/integrations/execution-callback` (signed webhook)

## Current repository structure

```text
.
|- docs/
|- scripts/
|- tests/
|- orchestrator/
|  |- app/
|  |  |- api/
|  |  |- services/
|  |  |- tasks/
|  |  |- templates/
|  |  |- static/
|  |- alembic/
|  |- Dockerfile
|- docker-compose.yml
|- docker-compose.dev.yml
|- modal_app.py
|- .env.example
```

## Recommended location for the GitHub repo/branch indexer

For your planned GitHub repository/branch indexer, the best fit is a **new top-level service folder**:

`/indexer`

Why this is the best fit:

- It has independent runtime behavior (scheduled sync/webhooks) from the build orchestrator.
- It isolates GitHub API rate-limit pressure away from build jobs.
- It can scale/deploy independently (or be disabled) without touching core request->build flow.
- It keeps `orchestrator/app/services` focused on request lifecycle, not catalog ingestion.

### Suggested indexer structure

```text
indexer/
|- app/
|  |- __init__.py
|  |- main.py                 # optional API (webhook + health)
|  |- config.py               # INDEXER_* env vars
|  |- db.py
|  |- models.py               # repo/branch/index_run tables
|  |- api/
|  |  |- routes.py            # /health, /webhooks/github
|  |- services/
|  |  |- github_client.py
|  |  |- repository_indexer.py
|  |  |- branch_indexer.py
|  |  |- sync_policy.py
|  |- tasks/
|  |  |- jobs.py              # enqueue + execution jobs
|  |- worker.py               # RQ/cron-like worker entrypoint
|- alembic/
|- alembic.ini
|- requirements.txt
|- Dockerfile
```

### Data model suggestion

Add indexer-owned tables:

- `github_repositories`
- `github_branches`
- `github_index_runs`

Keep them separate from feature request tables to avoid coupling orchestration logic to index cache internals.

### Integration contract with orchestrator

The orchestrator should read index data as a cache/hint layer only:

- repo validation during intake (`spec.repo`)
- base branch suggestions (`spec.base_branch`)
- optional Slack autocomplete support

If indexer data is stale/unavailable, orchestrator should gracefully fall back to direct GitHub lookups or current behavior.

### Docker Compose extension (planned)

When you add the indexer, extend compose with an `indexer` service that shares Postgres/Redis and runs independently from `api`/`worker`.

## Slack and GitHub setup docs

- Slack: `docs/SETUP_SLACK.md`
- GitHub: `docs/SETUP_GITHUB.md`
- Auth/RBAC: `docs/SETUP_AUTH.md`
- Modal: `docs/SETUP_MODAL.md`
- Architecture notes: `docs/ARCHITECTURE.md`
- Production posture: `docs/PRODUCTION_READINESS.md`
- Org deployment: `docs/ORG_DEPLOYMENT.md`

## Safety defaults

- `DISABLE_AUTOMERGE=true` by default.
- Intake is treated as untrusted input.
- Workspace isolation and retention controls are enabled and configurable.
- Signed callback verification is supported for external execution events.

## Development and tests

Run Python unit tests locally (Python 3.12):

```powershell
py -3.12 -m pytest -q
```

Main test suites are under `tests/unit` and include auth, state machine, GitHub adapters, callback idempotency, and workspace behavior.
