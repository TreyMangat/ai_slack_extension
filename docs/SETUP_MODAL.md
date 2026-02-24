# Modal Deployment (24/7)

This project now supports always-on deployment on Modal with:
- FastAPI API/UI (`modal_app.py::api`)
- Scheduled queue drain worker (`modal_app.py::drain_queue_once`)
- Scheduled cleanup worker (`modal_app.py::cleanup_once`)

Build flow is PR-first (no GitHub issue ticket creation).

## 1) Prerequisites

1. Modal account + CLI auth
- Install: `pip install modal`
- Login: `modal token new`

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
- `RUN_MIGRATIONS=true`
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

Recommended:
- `AUTH_MODE=edge_sso` (or `api_token`)
- `API_AUTH_TOKEN=...`
- `DISABLE_AUTOMERGE=true`

Slack on Modal (optional, recommended via HTTP mode):
- `ENABLE_SLACK_BOT=true`
- `SLACK_MODE=http`
- `SLACK_BOT_TOKEN=xoxb-...`
- `SLACK_SIGNING_SECRET=...`
- Set Slack Request URL to: `<BASE_URL>/api/slack/events`

## 3) Create Modal Secret

Create one secret containing all env vars above:

```powershell
modal secret create feature-factory-env `
  APP_ENV=prod `
  RUN_MIGRATIONS=true `
  MOCK_MODE=false `
  GITHUB_ENABLED=true `
  GITHUB_AUTH_MODE=app `
  GITHUB_APP_ID=123456 `
  GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----" `
  GITHUB_APP_SLUG=your-app-slug `
  DATABASE_URL="postgresql+psycopg2://..." `
  REDIS_URL="redis://..." `
  SECRET_KEY="replace-me" `
  INTEGRATION_WEBHOOK_SECRET="replace-me" `
  BASE_URL="https://<your-modal-url>"
```

## 4) Deploy

From repo root:

```powershell
modal deploy .\modal_app.py
```

This deploys:
- `api` (always-on container with `min_containers=1`)
- queue drainer schedule (every 20s)
- cleanup schedule (every 5m)

## 5) Configure Slack (Optional)

If using Slack on Modal:
1. In Slack app settings, use Events API / Slash command request URL pointing to your Modal API.
2. Keep `SLACK_MODE=http`.
3. Do not run the separate `slackbot` Socket Mode process in Modal for this mode.

## 6) GitHub App Install/User Flow

Each user should set target repo in the request (`spec.repo`, e.g. `org/repo`).

At build time:
- the system resolves the app installation for that repo dynamically
- mints installation token
- opens PR directly

If app is not installed on a repo, build fails with install guidance (using `GITHUB_APP_INSTALL_URL` or slug-derived URL).

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
