# Feature Factory (Local-first scaffold)

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
- Later wire in **Slack** (Socket Mode) + **GitHub** (issue + /oc trigger)
- Keep the orchestrator future-proof (clear adapters + state machine)

The Slack intake is novice-oriented:
- asks for problem + why now
- captures whether to build from scratch or reuse existing repo patterns
- captures source repos for safe reference cloning
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

## (Optional) Connect Slack (Socket Mode)

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
- `message.channels`
- `message.groups`
- `message.im`
- `message.mpim`

Add a Slash Command:
- `/feature`

### 2) Put tokens in `.env`

Set:
- `ENABLE_SLACK_BOT=true`
- `SLACK_BOT_TOKEN=...`
- `SLACK_APP_TOKEN=...`
- `REVIEWER_ALLOWED_USERS=U0123ABC,U0456DEF` (recommended)

(Optional but recommended)
- `SLACK_ALLOWED_CHANNELS=C0123ABC,C0456DEF`
- `SLACK_ALLOWED_USERS=U0123ABC,U0456DEF`
- `REVIEWER_CHANNEL_ID=C09REVIEW`

### 3) Restart

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack up --build
```

or:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack
```

Then in Slack, run:

```
/feature Add a button to export invoices
```

The bot will continue intake in thread (chat-first, no popup modal).

If thread replies are ignored, validate scopes/events:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1
```

---

## (Optional) Connect GitHub + OpenCode

This scaffold can create GitHub issues and post a comment that triggers OpenCode.
It can also run a native in-container LLM coding loop (experimental).

### 1) Prepare your target repo

- Create or choose a GitHub repo you want OpenCode to work on
- This repo now includes `.github/workflows/opencode-runner.yml`
- Push that workflow to the target repo so `/oc` comments trigger OpenCode automatically

### 1b) Set up OpenClaw locally (Codex OAuth)

Install/update and set model:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_openclaw.ps1
```

Complete OAuth in an interactive terminal:

```powershell
openclaw onboard --auth-choice openai-codex
# or
openclaw models auth login --provider openai-codex
```

Verify:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_openclaw_setup.ps1
```

### 2) Choose GitHub auth mode

For local testing:
- `GITHUB_AUTH_MODE=token`
- `GITHUB_TOKEN=<PAT with repo scope>`

For production:
- `GITHUB_AUTH_MODE=app`
- `GITHUB_APP_ID=...`
- `GITHUB_APP_INSTALLATION_ID=...`
- `GITHUB_APP_PRIVATE_KEY_PATH=...`
  - For Docker Compose, store key in `./secrets` and use `/run/secrets/<file>.pem`

### 2b) Choose code runner mode

- `CODERUNNER_MODE=opencode` (default): triggers external runner and expects signed callbacks.
- `CODERUNNER_MODE=native_llm` (experimental): the worker clones target repo, asks LLM for patch, runs tests, pushes branch, opens PR.

For `native_llm`, also set:
- `LLM_PROVIDER=openai`
- `LLM_API_KEY=...`
- `LLM_MODEL=gpt-4.1-mini` (or preferred model)
- `LLM_TEST_COMMAND=pytest -q` (or your repo's test command)
- `repo` in feature spec, or `GITHUB_REPO_OWNER` + `GITHUB_REPO_NAME`

For OpenClaw/OpenCode delegated mode (`CODERUNNER_MODE=opencode`):
- keep `MOCK_MODE=false`
- the GitHub workflow default model is `github-copilot/gpt-4.1` (no OpenAI key required)
- override with repo variable `OPENCODE_MODEL` if needed
- note: interactive OAuth is local-only; GitHub Actions runners cannot complete OAuth prompts.
  - for GitHub Actions, use non-interactive auth:
    - preferred no-key path: `COPILOT_GITHUB_TOKEN` (or built-in `GITHUB_TOKEN`)
    - API-key path: provider key such as `OPENAI_API_KEY`

### 3) Put GitHub config in `.env`

Set:
- `GITHUB_ENABLED=true`
- `GITHUB_AUTH_MODE=token|app`
- `GITHUB_TOKEN=...` (token mode)
- `GITHUB_APP_ID=...` (app mode)
- `GITHUB_APP_INSTALLATION_ID=...` (app mode)
- `GITHUB_APP_PRIVATE_KEY_PATH=...` (app mode)
- `GITHUB_REPO_OWNER=your-org-or-user`
- `GITHUB_REPO_NAME=your-repo`
- `WORKSPACE_ENABLE_GIT_CLONE=true` (if reuse mode should pull remote source repos)

### 4) Restart

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Now, when you run a build, the worker will:
- Create a GitHub issue
- Post the `/oc ...` trigger comment

OpenCode workflow secrets/variables in the target repo:
- `COPILOT_GITHUB_TOKEN` (recommended if using `github-copilot/*` models; falls back to Actions `GITHUB_TOKEN`)
- `OPENAI_API_KEY` (only if selected `OPENCODE_MODEL` needs OpenAI API key auth)
- `FEATURE_FACTORY_CALLBACK_URL` (optional, full URL or base URL of orchestrator)
- `FEATURE_FACTORY_WEBHOOK_SECRET` (optional, must match `INTEGRATION_WEBHOOK_SECRET`; set together with callback URL)
- `OPENCODE_MODEL` repo variable (optional override of default model)

If your external runner can call back, use:
- `POST /api/integrations/execution-callback`
- Signed with:
  - `X-Feature-Factory-Timestamp`
  - `X-Feature-Factory-Signature`
  - `X-Feature-Factory-Event-Id` (idempotency key)

### 5) Verify GitHub App install + permissions

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_github_app.ps1
```

Current required app permissions for this scaffold:
- `Issues: Read and write` (create issue + post trigger comment)
- `Metadata: Read`
- `Contents: Read` only if `WORKSPACE_ENABLE_GIT_CLONE=true`

Optional for future automation (not required today):
- `Pull requests: Read and write`

Helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\send_execution_callback.ps1 `
  -FeatureId "<feature-id>" `
  -Event preview_ready `
  -Secret "dev-webhook-secret" `
  -PreviewUrl "https://preview.example.com/123" `
  -GithubPrUrl "https://github.com/org/repo/pull/123"
```

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
- Replace mock adapters with real deployments:
  - preview environments (Vercel/Netlify/K8s)
  - stricter policy gates
  - GitHub webhooks for PR status

