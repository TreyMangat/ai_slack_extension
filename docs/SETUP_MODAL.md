# Modal Deployment (24/7)

This project now supports always-on deployment on Modal with:
- FastAPI API/UI (`modal_app.py::api`)
- Scheduled queue drain worker (`modal_app.py::drain_queue_once`)
- Scheduled cleanup worker (`modal_app.py::cleanup_once`)

Build flow is PR-first (no GitHub issue ticket creation).

## 1) Prerequisites

1. Modal account + CLI auth
- Install (Windows + Python 3.12): `py -3.12 -m pip install modal`
- Login: `py -3.12 -m modal token new`

2. Managed Postgres + Redis
- Set `DATABASE_URL` and `REDIS_URL` to managed services (not local Docker URLs).

3. GitHub App
- Create a GitHub App and install it on repos your users will target.
- Required permissions:
  - `Metadata: Read`
  - `Contents: Read and write`
  - `Pull requests: Read and write`
- Save:
  - `GITHUB_APP_ID`
  - private key PEM (`GITHUB_APP_PRIVATE_KEY` or path)
  - app slug (`GITHUB_APP_SLUG`) or explicit install URL (`GITHUB_APP_INSTALL_URL`)

## 2) Prepare Runtime Env

Use `.env.example` as the base and set production values.

Required values for Modal:
- `APP_ENV=prod`
- `APP_DISPLAY_NAME=PRFactory`
- `RUN_MIGRATIONS=false` (recommended for low-cost steady-state; run migrations manually during release)
- `MOCK_MODE=false`
- `GITHUB_ENABLED=true`
- `GITHUB_AUTH_MODE=app`
- `GITHUB_APP_ID=...`
- `GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----...`
- `DATABASE_URL=...`
- `REDIS_URL=...`
- `SECRET_KEY=...`
- `INTEGRATION_WEBHOOK_SECRET=...`
- `BASE_URL=<your modal api url>`

Choose one runtime coding strategy:
- `CODERUNNER_MODE=native_llm` (recommended on Modal) with:
  - `LLM_PROVIDER=openai`
  - `LLM_API_KEY=...`
  - `LLM_MODEL=...`
- or `CODERUNNER_MODE=opencode` + `OPENCODE_EXECUTION_MODE=local_openclaw`
  - requires OpenClaw auth files to exist in container runtime
  - optional hardening: `OPENCODE_NO_CHANGE_RETRY_ATTEMPTS=1`

Recommended:
- `AUTH_MODE=edge_sso` (or `api_token`)
- `API_AUTH_TOKEN=...`
- `DISABLE_AUTOMERGE=true`
- low-cost defaults (already set in `modal_app.py`, override only if needed):
  - `MODAL_API_MIN_CONTAINERS=0`
  - `MODAL_API_MAX_CONTAINERS=1`
  - `MODAL_API_ALLOW_CONCURRENT_INPUTS=8`
  - `MODAL_QUEUE_DRAIN_SECONDS=180`
  - `MODAL_CLEANUP_INTERVAL_MINUTES=120`
  - `MODAL_SKIP_WORKER_WHEN_QUEUE_EMPTY=true`

Slack on Modal (optional):
- HTTP mode:
  - `ENABLE_SLACK_BOT=true`
  - `SLACK_MODE=http`
  - `SLACK_REQUIRE_PROMPT_CONFIRMATION=true` (recommended: confirm optimized prompt before build starts)
  - `SLACK_SIGNING_SECRET=...`
  - `ENABLE_SLACK_OAUTH=true`
  - `SLACK_CLIENT_ID=...`
  - `SLACK_CLIENT_SECRET=...`
  - `SLACK_APP_ID=A...`
  - `SLACK_APP_CONFIG_TOKEN=xoxe.xoxp-...` (App Configuration Token from `https://api.slack.com/apps`)
  - `SLACK_APP_CONFIG_REFRESH_TOKEN=...` (for auto-rotating the short-lived config token)
  - optional fallback for one workspace: `SLACK_BOT_TOKEN=xoxb-...`
  - request URL + slash command URLs are synced automatically to `<BASE_URL>/api/slack/events`
  - OAuth callback URL is synced automatically to `<BASE_URL>/api/slack/oauth/callback`
  - add bot event `app_home_opened` for one-time setup DM
- Socket mode:
  - `ENABLE_SLACK_BOT=true`
  - `SLACK_MODE=socket`
  - `SLACK_BOT_TOKEN=xoxb-...`
  - `SLACK_APP_TOKEN=xapp-...`
  - run the separate socket worker process (not the FastAPI webhook route)

## 3) Create Modal Secret

Create one secret containing all env vars above:

```powershell
py -3.12 -m modal secret create feature-factory-env `
  APP_ENV=prod `
  RUN_MIGRATIONS=false `
  MOCK_MODE=false `
  GITHUB_ENABLED=true `
  GITHUB_AUTH_MODE=app `
  GITHUB_APP_ID=123456 `
  GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----" `
  GITHUB_APP_SLUG=your-app-slug `
  MODAL_API_MIN_CONTAINERS=0 `
  MODAL_API_MAX_CONTAINERS=1 `
  MODAL_API_ALLOW_CONCURRENT_INPUTS=8 `
  MODAL_QUEUE_DRAIN_SECONDS=180 `
  MODAL_CLEANUP_INTERVAL_MINUTES=120 `
  MODAL_SKIP_WORKER_WHEN_QUEUE_EMPTY=true `
  DATABASE_URL="postgresql+psycopg2://..." `
  REDIS_URL="redis://..." `
  SECRET_KEY="replace-me" `
  INTEGRATION_WEBHOOK_SECRET="replace-me" `
  BASE_URL="https://<your-modal-url>"
```

## 4) Deploy

From repo root:

```powershell
py -3.12 -m modal deploy .\modal_app.py
```

This deploys:
- `api` (scale-to-zero by default: `min_containers=0`)
- queue drainer schedule (every 180s by default)
- cleanup schedule (every 120m by default)

Production helper (recommended):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_modal_prod.ps1 -BaseUrl "https://<your-modal-url>"
```

The helper script:
- syncs `feature-factory-env` from `.env` and `slack-secret` only when `SLACK_BOT_TOKEN` is present
- syncs provider-specific secrets `NeonURL` and `feature-factory-redis`
- maps `NEONURL -> DATABASE_URL` and `UPSTASH_REDIS_URL -> REDIS_URL` when needed
- enforces `sslmode=require` for Neon URLs and `rediss://` for Upstash URLs
- enforces production-safe env defaults
- clears hardcoded Slack/GitHub allowlists and static repo targeting values for portable multi-user use
- loads GitHub App private key from local PEM when needed
- bundles `secrets/openclaw` into the Modal image when using `opencode/local_openclaw`
- auto-syncs Slack manifest URLs/events/commands (unless `-SkipSlackManifestSync`)
- deploys via Python 3.12 and verifies `/health`, `/health/ready`, and `/health/runtime`

## 5) Configure Slack (Optional)

If using Slack on Modal:
1. Keep `SLACK_MODE=http`.
2. Set `ENABLE_SLACK_OAUTH=true`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_APP_ID`, `SLACK_APP_CONFIG_TOKEN`, and `SLACK_APP_CONFIG_REFRESH_TOKEN` in `.env`.
3. Run deploy helper (it syncs Events/Interactivity/Slash-command URLs and OAuth callback URL automatically).
4. Share install link with external workspaces: `<BASE_URL>/api/slack/install`.
5. Do not run the separate `slackbot` Socket Mode process in Modal for this mode.

## 6) GitHub App Install/User Flow

Each user should set target repo in the request (`spec.repo`, e.g. `org/repo`).

At build time:
- the system resolves the app installation for that repo dynamically
- mints installation token
- opens PR directly

If app is not installed on a repo, build fails with install guidance (using `GITHUB_APP_INSTALL_URL` or slug-derived URL).

Per-user GitHub identity (recommended for shared channels):
- `ENABLE_GITHUB_USER_OAUTH=true`
- `GITHUB_OAUTH_CLIENT_ID=...`
- `GITHUB_OAUTH_CLIENT_SECRET=...`
- `GITHUB_USER_OAUTH_REQUIRED=true`
- optional: `GITHUB_USER_TOKEN_ENCRYPTION_KEY=...`

With this enabled, each Slack user connects their own GitHub account through:
- `<BASE_URL>/api/github/install?slack_user_id=<U...>&slack_team_id=<T...>`

## 7) Verify

Check health:
- `<BASE_URL>/health`
- `<BASE_URL>/health/ready`
- `<BASE_URL>/health/runtime`

Smoke flow:
1. Create feature request with `repo=org/repo`.
2. Run build.
3. Confirm `PR_OPENED` and PR URL appears.

## 8) Notes

- PR-only flow is enforced; issue-ticket kickoff flow was removed.
- `spec.repo` is the recommended per-request target (env repo remains fallback only).
