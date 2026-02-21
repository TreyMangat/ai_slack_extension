# Production Inputs

This file captures the current production-target configuration assumptions for this repository.

## Auth

- Approach: edge SSO in front of FastAPI
- Auth mode: `edge_sso`
- Provider: Cloudflare Access
- IdP: Google Workspace
- Trusted identity headers:
  - `X-Forwarded-Email`
  - `X-Forwarded-Groups`
- RBAC:
  - create/update spec: `any_authenticated`
  - run build: `group:engineering`
  - approve: `group:admins` (or `REVIEWER_ALLOWED_USERS` allowlist)

## Hosting

- Target: single VM
- Runtime stack:
  - `nginx` (TLS + reverse proxy)
  - `oauth2-proxy` or Cloudflare Access edge policy
  - docker compose services: `api`, `worker`, `cleanup`, optional `slackbot`, plus `db`/`redis`

## GitHub

- Auth mode: GitHub App
- Required values:
  - `GITHUB_APP_ID=2905646`
  - `GITHUB_APP_INSTALLATION_ID=111260503`
  - `GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/slack-ai-bot.private-key` (container path)
- Permissions:
  - Issues: write
  - (future) Pull Requests: read/write

## Cleanup

- Scheduled cleanup: every 60 minutes
- Retention policy: status-aware (`with PR`, `without PR`, `failed/needs-info`)

## Slack mode

- Runtime mode: Socket Mode (`SLACK_MODE=socket`)
