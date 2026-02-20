# Production Readiness Roadmap

This project is now stable for local testing, but these items are required for an organization-ready rollout.

## Current maturity

- Local orchestration flow works in `MOCK_MODE=true`.
- Queue, worker, and status transitions are verified by smoke tests.
- Signed execution callback endpoint exists for external CI/OpenCode status updates.
- Slack flow supports iterative clarifications (`NEEDS_INFO` -> `READY_FOR_BUILD`) via spec updates.
- Reuse mode prepares isolated workspace snapshots and logs `workspace_prepared` events.

## Required before production

1. Authentication and authorization
- Protect API/UI endpoints (SSO, service auth, RBAC).
- Restrict who can create/approve features (Slack user mapping + policy).
- Enforce reviewer approvals with `REVIEWER_ALLOWED_USERS`.
- For interim protection, set `API_AUTH_TOKEN` and require `X-FF-Token` on mutating `/api` routes.

2. External execution completion signals
- Wire your CI/OpenCode pipeline to call:
  - `POST /api/integrations/execution-callback`
- Use HMAC signing headers:
  - `X-Feature-Factory-Timestamp`
  - `X-Feature-Factory-Signature`
- Rotate `INTEGRATION_WEBHOOK_SECRET` via secrets manager.
- Enforce isolated workspace policy in the runner (clone/copy into sandbox; no direct pushes).

3. GitHub integration hardening
- Use GitHub App credentials instead of long-lived PATs.
- Add retry/backoff and dead-letter handling around API calls.

4. Data and schema management
- Add Alembic migrations and migration CI checks.
- Add backup/restore strategy for Postgres.

5. Observability
- Structured JSON logs and request/job correlation IDs.
- Metrics and alerts for failed builds, callback rejects, and queue latency.

6. Security controls
- Secret storage in a managed vault.
- Enforce TLS at ingress.
- Audit trail retention policy and PII handling policy.

7. Workspace lifecycle controls
- Add cleanup jobs for `WORKSPACE_ROOT` (TTL/retention).
- Add storage quotas to avoid large repo snapshot growth.
- Keep local snapshot roots restricted and monitored.
- Use status-aware retention so rejected/non-PR work expires faster than PR-backed work.

## Suggested release sequence

1. Local validation (`smoke_test.ps1` + `real_mode_callback_smoke.ps1`).
2. Dev Slack workspace + dev GitHub org/repo.
3. Staging with real secrets and callback pipeline.
4. Production with change-management and on-call alerting.
