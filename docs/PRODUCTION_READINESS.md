# Production Readiness Roadmap

This project is now stable for local testing, but these items are required for an organization-ready rollout.

## Current maturity

- Local orchestration flow works in `MOCK_MODE=true`.
- Queue, worker, and status transitions are verified by smoke tests.
- Signed execution callback endpoint exists for external CI/OpenCode status updates.
- Stale callback detector emits `callback_stale_alerted` events when builds remain in `PR_OPENED`.
- Slack flow supports iterative clarifications (`NEEDS_INFO` -> `READY_FOR_BUILD`) via spec updates.
- Reuse mode prepares isolated workspace snapshots and logs `workspace_prepared` events.
- GitHub Actions now enforce a risk-aware preflight policy and head-SHA freshness checks before merge.
- Edge-SSO-ready auth and RBAC enforcement exist for UI/API routes.
- Alembic baseline migration exists with production startup migration path.
- Scheduled workspace cleanup worker runs independently from build jobs.
- GitHub App auth mode is supported (PAT mode retained for local dev).

## Required before production

1. Authentication and authorization
- Deploy edge auth (Cloudflare Access or oauth2-proxy) to provide trusted identity headers.
- Map IdP groups to `RBAC_BUILDERS` and `RBAC_APPROVERS`.
- Keep `API_AUTH_TOKEN` enabled for internal service-to-service calls (for example `slackbot` -> `api`).

2. External execution completion signals
- Wire your CI/OpenCode pipeline to call:
  - `POST /api/integrations/execution-callback`
- Use HMAC signing headers:
  - `X-Feature-Factory-Timestamp`
  - `X-Feature-Factory-Signature`
  - `X-Feature-Factory-Event-Id` (required idempotency key)
- Keep callback staleness thresholds tuned:
  - `CALLBACK_STALE_ALERT_MINUTES`
  - `CALLBACK_STALE_ALERT_COOLDOWN_MINUTES`
- Rotate `INTEGRATION_WEBHOOK_SECRET` via secrets manager.
- Enforce isolated workspace policy in the runner (clone/copy into sandbox; no direct pushes).
- Ensure callback payloads include deterministic `event_id` values for replay-safe dedupe.

3. GitHub integration hardening
- Configure GitHub App credentials in secrets manager (preferred over PATs).
- Add retry/backoff and dead-letter handling around API calls.

4. Data and schema management
- Add migration CI checks (current baseline and runtime migration path are implemented).
- Add backup/restore strategy for Postgres.

5. Observability
- Structured JSON logs and request/job correlation IDs.
- Metrics and alerts for failed builds, callback rejects, and queue latency.

6. Security controls
- Secret storage in a managed vault.
- Enforce TLS at ingress.
- Audit trail retention policy and PII handling policy.

7. Workspace lifecycle controls
- Tune cleanup cadence (`WORKSPACE_CLEANUP_INTERVAL_MINUTES`) and retention values.
- Add storage quotas to avoid large repo snapshot growth.
- Keep local snapshot roots restricted and monitored.
- Use status-aware retention so rejected/non-PR work expires faster than PR-backed work.

## Suggested release sequence

1. Local validation (`smoke_test.ps1` + `real_mode_callback_smoke.ps1`).
2. Dev Slack workspace + dev GitHub org/repo.
3. Staging with real secrets and callback pipeline.
4. Production with change-management and on-call alerting.
