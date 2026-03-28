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

With Slack + Repo_Indexer profile:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack -WithIndexer
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

## Repo Indexer integration

PRFactory now supports an external Repo_Indexer deployment through HTTP, without sharing code between repos.

Supported integration points:

- Slack command: `/prfactory-indexer <query>` for ranked repo/evidence search.
- Slack intake repo/branch suggestions: uses Repo_Indexer catalog first, then falls back to direct GitHub API calls.
- Graceful fallback: if indexer is unavailable, existing PRFactory behavior continues.

Environment variables:

- `INDEXER_BASE_URL` (example: `http://indexer-api:8080` inside docker-compose, or `http://localhost:8080` from host tools)
- `INDEXER_AUTH_TOKEN` (optional)
- `INDEXER_TIMEOUT_SECONDS` (default `4`)
- `INDEXER_TOP_K_REPOS` (default `5`)
- `INDEXER_TOP_K_CHUNKS` (default `3`)
- `INDEXER_TOP_K_BRANCHES_PER_REPO` (default `8`)
- `INDEXER_REQUIRED` (optional; when true, startup/deploy fails if indexer URL is missing)

Hosted production deploy with indexer requirement:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_modal_prod.ps1 -BaseUrl "https://<your-modal-url>" -RequireIndexer
```

Optional local compose extension for sibling repo checkout:

- `docker-compose.indexer.yml` (expects `../Repo_Indexer`)
- helper script: `powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack -WithIndexer`

When using the compose extension, set `INDEXER_BASE_URL=http://indexer-api:8080` in PRFactory `.env`.

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
