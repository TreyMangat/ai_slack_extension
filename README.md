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
- supports iterative updates through an **Add details** action
- routes preview/PR output to reviewer/admin approval

---

## What you get

- **FastAPI** web app (local UI + JSON API)
- **Postgres** database
- **Redis + RQ** worker for background jobs
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

### 4) Start the stack

In VS Code Terminal (PowerShell):

```powershell
docker compose up --build
```

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

This verifies the core API workflow end-to-end:
- health check
- spec validation
- build enqueue + worker transition
- preview readiness
- product approval

### 7) (Optional) Verify real-mode callback flow

This validates the non-mock execution path where an external runner posts status back.

1. Set in `.env`:
   - `MOCK_MODE=false`
   - `INTEGRATION_WEBHOOK_SECRET=dev-webhook-secret`
2. Restart:

```powershell
docker compose up --build
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
- `channels:join` (recommended, lets bot join public channels)
- `groups:read`
- `im:read`
- `mpim:read`

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
docker compose --profile slack up --build
```

Then in Slack, run:

```
/feature Add a button to export invoices
```

---

## (Optional) Connect GitHub + OpenCode

This scaffold can create GitHub issues and post a comment that triggers OpenCode.

### 1) Prepare your target repo

- Create or choose a GitHub repo you want OpenCode to work on
- Install the **OpenCode GitHub integration / Action** in that repo

### 2) Create a GitHub token

For testing you can use a **classic Personal Access Token** (PAT) with:
- `repo` scope (private repos)

### 3) Put GitHub config in `.env`

Set:
- `GITHUB_ENABLED=true`
- `GITHUB_TOKEN=...`
- `GITHUB_REPO_OWNER=your-org-or-user`
- `GITHUB_REPO_NAME=your-repo`
- `WORKSPACE_ENABLE_GIT_CLONE=true` (if reuse mode should pull remote source repos)

### 4) Restart

```powershell
docker compose up --build
```

Now, when you run a build, the worker will:
- Create a GitHub issue
- Post the `/oc ...` trigger comment

If your external runner can call back, use:
- `POST /api/integrations/execution-callback`
- Signed with:
  - `X-Feature-Factory-Timestamp`
  - `X-Feature-Factory-Signature`

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
- `orchestrator/app/slackbot.py` - Slack Socket Mode bot (optional)

---

## Safety notes (important)

- Auto-merge is disabled by default (`DISABLE_AUTOMERGE=true`).
- Slack and GitHub adapters are designed to be least-privilege.
- Treat Slack messages and attachments as **untrusted input**.
- Optional API auth: set `API_AUTH_TOKEN` and pass `X-FF-Token` for mutating `/api` calls.

---

## Next steps

- Edit `docs/ARCHITECTURE.md` to match your real infrastructure
- Review `docs/PRODUCTION_READINESS.md` for org-grade rollout requirements
- Replace mock adapters with real deployments:
  - preview environments (Vercel/Netlify/K8s)
  - stricter policy gates
  - GitHub webhooks for PR status

