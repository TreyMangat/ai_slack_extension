# Auth and RBAC Setup

This project supports edge-authenticated deployments where identity is injected by a proxy in front of FastAPI.

## Modes

- `AUTH_MODE=disabled`
  - local development convenience
- `AUTH_MODE=api_token`
  - requires `X-FF-Token` for protected UI/API routes
- `AUTH_MODE=edge_sso`
  - requires trusted identity headers
  - supports service token (`X-FF-Token`) for internal callers (for example `slackbot`)

## Trusted headers

Defaults:

- `AUTH_HEADER_EMAIL=X-Forwarded-Email`
- `AUTH_HEADER_GROUPS=X-Forwarded-Groups`

Protected routes:

- all UI routes
- all `/api/*` routes

Exempt routes:

- `/health`
- `/health/ready`
- `/api/integrations/execution-callback`

## RBAC

Rules are comma-separated OR logic:

- `any_authenticated`
- `group:<name>`
- `user:<identity>`

Defaults:

- `RBAC_REQUESTERS=any_authenticated`
- `RBAC_BUILDERS=group:engineering`
- `RBAC_APPROVERS=group:admins`

`REVIEWER_ALLOWED_USERS` is also accepted for approvals.

## Service-to-service calls

For internal services that do not pass through edge auth:

- set `API_AUTH_TOKEN`
- send `X-FF-Token`
- optionally set `X-Feature-Factory-Actor` (or `AUTH_SERVICE_ACTOR_HEADER`) for audit identity

`slackbot` uses this path automatically when `API_AUTH_TOKEN` is set.
