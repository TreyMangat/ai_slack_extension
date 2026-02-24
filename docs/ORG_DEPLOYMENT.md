# Organization Deployment Guide

This document describes how to run Feature Factory for an organization without using end-user laptops.

## 1) Runtime model (server-side only)

Run the full stack on a dedicated VM or server:
- `api` (FastAPI)
- `worker` (RQ background jobs)
- `cleanup` (scheduled workspace retention worker)
- `slackbot` (Socket Mode event handler)
- `postgres` (or managed Postgres)
- `redis` (or managed Redis)

Compose profile split:
- `docker-compose.yml`: production-safe defaults
- `docker-compose.dev.yml`: local-only overrides (hot reload + host DB/Redis ports)

Recommended production split:
- App VM(s): `api`, `worker`, `slackbot`
- Managed services: Postgres + Redis
- Reverse proxy / ingress: TLS + auth

## 1.1) Edge auth + RBAC model

Recommended for your setup:
- `AUTH_MODE=edge_sso`
- trusted identity headers from edge: `X-Forwarded-Email`, `X-Forwarded-Groups`
- optional service token (`X-FF-Token`) for internal callers such as `slackbot`

Route protection:
- Protected: all UI routes and `/api/*`
- Exempt: `/health`, `/health/ready`, `/api/integrations/execution-callback`

RBAC defaults:
- `RBAC_REQUESTERS=any_authenticated`
- `RBAC_BUILDERS=group:engineering`
- `RBAC_APPROVERS=group:admins`
- `REVIEWER_ALLOWED_USERS` may additionally authorize approvals

## 2) How code is actually generated

Current behavior:
- In `MOCK_MODE=true`, the code runner is simulated and returns mock PR/preview URLs.
- In `MOCK_MODE=false`, orchestrator runs the code runner and opens a PR directly.
- External runner (OpenCode/CI) performs the real coding work and calls back to:
  - `POST /api/integrations/execution-callback`
  - include `X-Feature-Factory-Event-Id` for idempotent replay-safe callbacks

So, production code generation does not happen on end-user machines; it happens in your external runner/CI.

## 3) GitHub review flow

1. Request created (Slack/UI/API) -> spec validated.
2. Build starts -> isolated workspace snapshot prepared.
3. Code runner opens PR from isolated branch.
4. Preview/callback updates are posted to orchestrator.
5. Reviewer/admin approves in Slack/UI (`REVIEWER_ALLOWED_USERS` enforced).
6. Feature advances to merge-ready states.

Auto-merge remains disabled by default (`DISABLE_AUTOMERGE=true`).

GitHub auth modes:
- Local/dev: `GITHUB_AUTH_MODE=token` + `GITHUB_TOKEN`
- Production: `GITHUB_AUTH_MODE=app` + app ID, installation ID, private key

## 4) Storage lifecycle

Persisted:
- Postgres: feature requests, state transitions, audit events.
- GitHub: PRs/branches (if real integration is used).

Ephemeral/local:
- Workspace snapshots under `WORKSPACE_ROOT`.

Retention policy (status-aware):
- `WORKSPACE_RETENTION_HOURS_WITH_PR` (default 168h)
- `WORKSPACE_RETENTION_HOURS_WITHOUT_PR` (default 24h)
- `WORKSPACE_RETENTION_HOURS_FAILED` (default 12h)
- `WORKSPACE_CLEANUP_INTERVAL_MINUTES` (default 60)

This gives admins review time while automatically expiring rejected/non-promoted work faster.

## 4.1) Database migrations

- Alembic baseline is included under `orchestrator/alembic`.
- Startup behavior:
  - `APP_ENV=prod` or `RUN_MIGRATIONS=true` -> `alembic upgrade head`
  - local/dev defaults -> SQLAlchemy `create_all` convenience path
- Transitional helper for pre-Alembic DBs:
  - set `MIGRATION_BOOTSTRAP_STAMP=true` once to stamp existing schema to head
- Manual migration command:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1`

## 5) Minimum org hardening checklist

- Set `API_AUTH_TOKEN` and require `X-FF-Token` for mutating API calls.
- Restrict Slack usage with `SLACK_ALLOWED_CHANNELS`, `SLACK_ALLOWED_USERS`.
- Restrict approvals with `REVIEWER_ALLOWED_USERS`.
- Use GitHub App credentials (not long-lived PATs) for production.
- Keep secrets in a secrets manager (not plaintext env files in repos).
- Add backup and retention policy for Postgres.
