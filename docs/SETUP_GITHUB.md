# GitHub Setup (PR-only flow)

This project now creates PRs directly (no GitHub issue ticket kickoff).

## 1) Choose auth mode

### Local/dev (PAT)

1. Create a PAT:
- private repos: `repo`
- public repos: `public_repo`
2. In `.env` set:
- `GITHUB_ENABLED=true`
- `GITHUB_AUTH_MODE=token`
- `GITHUB_TOKEN=...`

### Production (GitHub App, recommended)

In `.env` set:
- `GITHUB_ENABLED=true`
- `GITHUB_AUTH_MODE=app`
- `GITHUB_APP_ID=...`
- `GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/feature_factory_github_app.pem`
  - or `GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----...`
- `GITHUB_APP_SLUG=...` (recommended)
  - optional explicit override: `GITHUB_APP_INSTALL_URL=...`
- optional static installation fallback: `GITHUB_APP_INSTALLATION_ID=...`

Container note:
- `docker-compose.yml` mounts `./secrets` to `/run/secrets` (read-only).
- If using `GITHUB_APP_PRIVATE_KEY_PATH`, place key file under local `secrets/`.

## 2) Repo targeting (multi-user ready)

Recommended:
- each request sets `spec.repo` as `org/repo`
- runtime resolves GitHub App installation dynamically for that repo

Fallback:
- configure `GITHUB_REPO_OWNER` and `GITHUB_REPO_NAME`

If app is not installed on the requested repo, build fails with install/login guidance URL.
In Slack, users can also run `/prfactory-github` to get the install/login link.

## 3) Required GitHub App permissions

- `Metadata: Read`
- `Contents: Read and write`
- `Pull requests: Read and write`

## 4) Validate setup

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_github_app.ps1
```

## 5) Code runner modes

- `CODERUNNER_MODE=opencode` + `OPENCODE_EXECUTION_MODE=local_openclaw` (default)
- `CODERUNNER_MODE=native_llm` (experimental)

Both modes target a repo, push a feature branch, and open a PR directly.

## 6) Callback integration (optional)

If external systems need to post PR/preview status updates:

- configure:
  - `INTEGRATION_WEBHOOK_SECRET=...`
  - `INTEGRATION_WEBHOOK_TTL_SECONDS=300`
- endpoint:
  - `POST /api/integrations/execution-callback`
- required headers:
  - `X-Feature-Factory-Timestamp`
  - `X-Feature-Factory-Signature` (`sha256=<hmac-hex>`)
  - `X-Feature-Factory-Event-Id`
