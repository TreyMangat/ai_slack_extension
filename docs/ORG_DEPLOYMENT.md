# Organization Deployment Guide

This document describes how to run Feature Factory for an organization without using end-user laptops.

## 1) Runtime model (server-side only)

Run the full stack on a dedicated VM or server:
- `api` (FastAPI)
- `worker` (RQ background jobs)
- `slackbot` (Socket Mode event handler)
- `postgres` (or managed Postgres)
- `redis` (or managed Redis)

Recommended production split:
- App VM(s): `api`, `worker`, `slackbot`
- Managed services: Postgres + Redis
- Reverse proxy / ingress: TLS + auth

## 2) How code is actually generated

Current behavior:
- In `MOCK_MODE=true`, the code runner is simulated and returns mock PR/preview URLs.
- In `MOCK_MODE=false`, orchestrator creates a GitHub issue and posts the OpenCode trigger comment.
- External runner (OpenCode/CI) performs the real coding work and calls back to:
  - `POST /api/integrations/execution-callback`

So, production code generation does not happen on end-user machines; it happens in your external runner/CI.

## 3) GitHub review flow

1. Request created (Slack/UI/API) -> spec validated.
2. Build starts -> isolated workspace snapshot prepared.
3. GitHub issue created (+ OpenCode trigger comment in real mode).
4. External runner opens PR + preview and sends signed callback.
5. Reviewer/admin approves in Slack/UI (`REVIEWER_ALLOWED_USERS` enforced).
6. Feature advances to merge-ready states.

Auto-merge remains disabled by default (`DISABLE_AUTOMERGE=true`).

## 4) Storage lifecycle

Persisted:
- Postgres: feature requests, state transitions, audit events.
- GitHub: issues/PRs/branches (if real integration is used).

Ephemeral/local:
- Workspace snapshots under `WORKSPACE_ROOT`.

Retention policy (status-aware):
- `WORKSPACE_RETENTION_HOURS_WITH_PR` (default 168h)
- `WORKSPACE_RETENTION_HOURS_WITHOUT_PR` (default 24h)
- `WORKSPACE_RETENTION_HOURS_FAILED` (default 12h)

This gives admins review time while automatically expiring rejected/non-promoted work faster.

## 5) Minimum org hardening checklist

- Set `API_AUTH_TOKEN` and require `X-FF-Token` for mutating API calls.
- Restrict Slack usage with `SLACK_ALLOWED_CHANNELS`, `SLACK_ALLOWED_USERS`.
- Restrict approvals with `REVIEWER_ALLOWED_USERS`.
- Use GitHub App credentials (not long-lived PATs) for production.
- Keep secrets in a secrets manager (not plaintext env files in repos).
- Add backup and retention policy for Postgres.
