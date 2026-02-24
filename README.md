# PRFactory (Local + Modal scaffold)

This project is a **local-first** (Docker Compose) scaffold for the workflow you described:

1. A non-dev submits a feature request (Slack or local web UI)
2. The system asks clarifying follow-up questions until the request is a **validated spec**
3. A build job runs (mocked locally by default)
4. The worker prepares an isolated workspace snapshot (for safe repo reuse)
5. A PR + preview link are produced (mocked locally by default)
6. A requester can approve in Slack/UI
7. Merge is controlled by policy (auto-merge is **disabled by default**)

It is designed so you can:
- **Start in MOCK_MODE** without Slack/GitHub
- Later wire in **Slack** + **GitHub App** for direct PR generation (no issue ticket required)
- Keep the orchestrator future-proof (clear adapters + state machine)

The Slack intake is novice-oriented:
- asks only for what to build + target repo in the default flow
- defaults missing fields automatically for local POC speed
- auto-starts build when the request is valid (no extra confirmation step)
- posts clarifying questions when details are missing
- supports iterative updates through an **Add details in chat** action
- routes preview/PR output to reviewer/admin approval

---

## What you get

- **FastAPI** web app (local UI + JSON API)
- **Postgres** database
- **Redis + RQ** worker for background jobs
- **Cleanup worker** for scheduled workspace retention
- Optional **Slack bot** process (disabled by default)
- Pluggable adapters for Slack, GitHub, Code Runner, Preview

---

## Quick Start (Windows, novice-friendly)

### 1) Install required software

1. **Docker Desktop for Windows**
   - Install and make sure Docker is running.
2. **Git for Windows**
   - Needed to clone repos and for VS Code integrations.
3. **VS Code**
   - Open this folder in VS Code.

> You do **not** need to install Python locally because everything runs in Docker.

### 2) Unzip / open the project

- Unzip the project
- Open this project folder in VS Code

### 3) Create your `.env`

In the project root:

- Copy `.env.example` to `.env`

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Leave everything as-is for now (MOCK_MODE is enabled by default).
Local defaults keep `AUTH_MODE=disabled`; production should use `AUTH_MODE=edge_sso`.

### 4) Start the stack

In VS Code Terminal (PowerShell):

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
```

`docker-compose.yml` is production-safe by default (no hot reload, no host DB/Redis ports).
`docker-compose.dev.yml` adds local developer overrides.
If you are reusing an older local DB volume after schema changes, run `scripts/migrate.ps1`
or reset with `scripts/reset_db.ps1`.

### 5) Open the local UI

Go to:

- http://localhost:8000
- http://localhost:8000/health/ready (readiness check)

Create a feature request, then click **Run Build** to simulate a PR + preview.
If the request lands in `NEEDS_INFO`, open the feature page and use **Save and revalidate** to fill missing details.

### 6) Run the smoke test script

In a second PowerShell terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke_test.ps1
```

The smoke script now reads `/health/runtime` to detect the API's effective `MOCK_MODE`
so checks stay accurate even when `.env` and running containers drift.

This verifies the core API workflow end-to-end:
- health check
- spec validation
- build enqueue + worker transition
- preview readiness
- product approval

If you run local unit tests outside Docker, use Python 3.12:

```powershell
py -3.12 -m pytest -q
```

### 7) (Optional) Run Alembic migration path locally

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1
```

This exercises the production migration path (`alembic upgrade head`).
If your local DB was created before Alembic, run once with bootstrap stamping:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1 -BootstrapStamp
```

### 8) (Optional) Verify real-mode callback flow

This validates the non-mock execution path where an external runner posts status back.

1. Set in `.env`:
   - `MOCK_MODE=false`
   - `INTEGRATION_WEBHOOK_SECRET=dev-webhook-secret`
2. Restart:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

3. Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\real_mode_callback_smoke.ps1 -Secret "dev-webhook-secret"
```

---

## What "MOCK_MODE" means

When `MOCK_MODE=true`:
- No Slack tokens required
- No GitHub token required
- The "code runner" simulates creating:
  - a PR URL
  - a preview URL
- This lets you test the end-to-end orchestration and state machine locally.

---

## Reuse mode and safe snapshots

When `implementation_mode=reuse_existing`, each build prepares an isolated workspace:
- `target/` for generated changes
- `references/` for source repo snapshots
- `workspace_manifest.json` for audit/debug data

Safety defaults:
- `WORKSPACE_ENABLE_GIT_CLONE=false` (remote clone disabled unless explicitly enabled)
- local path snapshots are restricted to `WORKSPACE_LOCAL_COPY_ROOT`
- `.git` metadata is removed from snapshots
- retention policy supports shorter storage for failed/non-PR work:
  - `WORKSPACE_RETENTION_HOURS_WITH_PR`
  - `WORKSPACE_RETENTION_HOURS_WITHOUT_PR`
  - `WORKSPACE_RETENTION_HOURS_FAILED`

---

## (Optional) Connect Slack

Only do this after the mock flow works.

### 1) Create a Slack app

In Slack API dashboard:
- Create an app
- Enable **Socket Mode**
- Create an **App Token** (starts with `xapp-...`) with `connections:write`
- Add a **Bot Token** (starts with `xoxb-...`) via OAuth & Permissions

Recommended bot scopes (minimum viable):
- `chat:write`
- `commands`
- `channels:read`
- `channels:history`
- `channels:join` (recommended, lets bot join public channels)
- `groups:read`
- `groups:history`
- `im:read`
- `im:history`
- `mpim:read`
- `mpim:history`

Event subscriptions (bot events):
- `app_home_opened`
- `member_joined_channel`
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`

Add slash commands:
- `/prfactory`
- `/feature` (legacy alias)
- `/prfactory-github`

### 2) Put tokens in `.env`

Set:
- `ENABLE_SLACK_BOT=true`
- `SLACK_MODE=http` (for Modal/cloud)
- `SLACK_SIGNING_SECRET=...`
- `ENABLE_SLACK_OAUTH=true`
- `SLACK_CLIENT_ID=...`
- `SLACK_CLIENT_SECRET=...`
- `SLACK_APP_ID=...`
- `SLACK_APP_CONFIG_TOKEN=...` (App Configuration Token from `https://api.slack.com/apps`, usually `xoxe.xoxp-...`)
- `SLACK_BOT_TOKEN=...` (optional single-workspace fallback)
- `SLACK_APP_TOKEN=...` (only required for local `SLACK_MODE=socket`)

(Optional)
- `REVIEWER_CHANNEL_ID=C09REVIEW`

### 3) Choose runtime mode

Local Docker (Socket Mode):

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack up --build
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack
```

Modal/cloud (HTTP mode):
- Keep `SLACK_MODE=http`
- Run `py -3.12 .\scripts\sync_slack_manifest.py --env-file .env` to auto-sync URLs/events/commands/callback
- Do not run the separate socket-mode `slackbot` process
- Share install URL for external workspaces: `<BASE_URL>/api/slack/install`

Scope notes:
- `SLACK_APP_CONFIG_TOKEN` configures the app (not channel-specific).
- In OAuth mode, each installed workspace gets its own bot token.
- Any channel can use the bot after invite (no allowlist in production deploy script).

Then in Slack, run:

```
/prfactory Add a button to export invoices
```

The bot will continue intake in thread (chat-first, no popup modal), then auto-start build once required fields are captured.

If thread replies are ignored, validate scopes/events:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1
```

---

## (Optional) Connect GitHub (PR-first flow)

Current behavior in real mode:
- no GitHub issue ticket is created
- worker opens a PR directly
- callback endpoint is still available for preview/build status updates

### 1) Configure GitHub auth

Local testing:
- `GITHUB_AUTH_MODE=token`
- `GITHUB_TOKEN=<PAT with repo scope>`

Production repository access:
- `GITHUB_AUTH_MODE=app`
- `GITHUB_APP_ID=...`
- `GITHUB_APP_PRIVATE_KEY_PATH=...` (or `GITHUB_APP_PRIVATE_KEY=...`)
- `GITHUB_APP_SLUG=...` (recommended, used for install/login guidance)

Per-user GitHub identity (recommended for shared Slack channels):
- `ENABLE_GITHUB_USER_OAUTH=true`
- `GITHUB_OAUTH_CLIENT_ID=...`
- `GITHUB_OAUTH_CLIENT_SECRET=...`
- `GITHUB_USER_OAUTH_REQUIRED=true`
- optional: `GITHUB_USER_TOKEN_ENCRYPTION_KEY=...` (otherwise derived from `SECRET_KEY`)

With the settings above, each Slack user must connect their own GitHub account before build.
PRFactory no longer uses one shared GitHub identity for all users in the same Slack workspace/channel.

### 2) Configure target repo strategy

Recommended (multi-user):
- each request includes `spec.repo` (`org/repo`)
- app installation is resolved dynamically for that repo

Fallback:
- set `GITHUB_REPO_OWNER` + `GITHUB_REPO_NAME`

### 3) Choose code runner mode

- `CODERUNNER_MODE=opencode` + `OPENCODE_EXECUTION_MODE=local_openclaw` (default):
  - clones target repo, generates code, pushes branch, opens PR.
- `CODERUNNER_MODE=native_llm` (experimental):
  - in-container LLM patch/test/push/PR flow.

### 4) Validate GitHub App setup

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_github_app.ps1
```

Required app permissions:
- `Metadata: Read`
- `Contents: Read and write`
- `Pull requests: Read and write`

### 5) Modal deployment

For 24/7 hosting on Modal, follow:
- `docs/SETUP_MODAL.md`
- For exact coworker onboarding + invite flow, use `docs/ONBOARDING_PRFACTORY.md`

---

## Project layout

- `orchestrator/app/main.py` - FastAPI entrypoint
- `orchestrator/app/models.py` - DB models
- `orchestrator/app/services/*` - adapters and business logic
- `orchestrator/app/tasks/jobs.py` - queued background jobs
- `orchestrator/app/worker.py` - RQ worker
- `orchestrator/app/cleanup_worker.py` - scheduled workspace cleanup worker
- `orchestrator/app/slackbot.py` - Slack Socket Mode bot (optional)

---

## Safety notes (important)

- Auto-merge is disabled by default (`DISABLE_AUTOMERGE=true`).
- Slack and GitHub adapters are designed to be least-privilege.
- Treat Slack messages and attachments as **untrusted input**.
- Auth/RBAC are configurable:
  - local: `AUTH_MODE=disabled`
  - edge SSO: `AUTH_MODE=edge_sso` + trusted headers (`X-Forwarded-Email`, `X-Forwarded-Groups`)
  - service calls: `API_AUTH_TOKEN` with `X-FF-Token`
- Scheduled cleanup is independent from build flow (`WORKSPACE_CLEANUP_INTERVAL_MINUTES`).

---

## Next steps

- Edit `docs/ARCHITECTURE.md` to match your real infrastructure
- Review `docs/PRODUCTION_READINESS.md` for org-grade rollout requirements
- Use `docs/ORG_DEPLOYMENT.md` for server/VM deployment and storage lifecycle policy
- Use `docs/SETUP_AUTH.md` for edge SSO + RBAC configuration
- Track finalized deployment assumptions in `PRODUCTION_INPUTS.md`
- Use `docs/code-factory.md` for risk-aware PR gating and review-agent operations
- Use `docs/MODEL_PROVIDERS.md` for multi-provider runner strategy (OpenAI/Claude/Gemini)
- Use `docs/OPENCLAW_AUTH.md` for container auth mounting and no-key Codex OAuth flow
- Use `docs/PREVIEW_DEPLOYS.md` for PR preview deployment setup (Cloudflare Pages recommended)
- Replace mock adapters with real deployments:
  - preview environments (Vercel/Netlify/K8s)
  - stricter policy gates
  - GitHub webhooks for PR status

