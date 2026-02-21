# GitHub setup

## Minimal local testing (PAT mode)

You can enable GitHub issue creation without OpenCode using a Personal Access Token.

1. Create a PAT:
   - private repos: `repo`
   - public repos: `public_repo`
2. In `.env` set:
   - `GITHUB_ENABLED=true`
   - `GITHUB_AUTH_MODE=token`
   - `GITHUB_TOKEN=...`
   - `GITHUB_REPO_OWNER=...`
   - `GITHUB_REPO_NAME=...`
3. Restart docker compose.

## Production mode (GitHub App)

Prefer GitHub App auth for org deployments.

In `.env` set:

- `GITHUB_ENABLED=true`
- `GITHUB_AUTH_MODE=app`
- `GITHUB_APP_ID=...`
- `GITHUB_APP_INSTALLATION_ID=...`
- `GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/feature_factory_github_app.pem`
  - or `GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----...`

Container note:
- `docker-compose.yml` mounts `./secrets` to `/run/secrets` (read-only).
- Place your key file under local `secrets/` and reference it with container path (not host path).
- `GITHUB_REPO_OWNER=...`
- `GITHUB_REPO_NAME=...`

Required app permissions:
- Issues: write
- Metadata: read

Conditionally required:
- Contents: read (only if `WORKSPACE_ENABLE_GIT_CLONE=true`)

Optional for future merge/PR automation:
- Pull requests: read/write

Required when using `CODERUNNER_MODE=native_llm`:
- Pull requests: write (to open PRs after generated commits)
- Contents: write (to push generated branch commits)

Validation command:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_github_app.ps1
```

## With OpenCode

This scaffold triggers OpenCode by posting a comment to the created issue.
It also includes build context (implementation mode + referenced repos) so your runner can choose safe clone/copy workflows.

This repo ships with `.github/workflows/opencode-runner.yml`, which:
- listens for `/oc` issue comments
- runs `anomalyco/opencode/github@latest`
- optionally sends signed callback updates to Feature Factory

For reuse mode, the orchestrator also prepares local workspace snapshots and writes a manifest:
- `WORKSPACE_ROOT=/tmp/feature_factory_workspaces`
- `WORKSPACE_ENABLE_GIT_CLONE=false` by default (recommended until reviewed)
- `WORKSPACE_LOCAL_COPY_ROOT=/app` restricts local snapshot scope

Set the trigger comment:

- `OPENCODE_TRIGGER_COMMENT=/oc Implement this issue. Follow acceptance criteria. Add tests.`

Recommended repo settings for the workflow:

- Repo variable: `OPENCODE_MODEL=github-copilot/gpt-4.1` (default if unset)
- Secret: `COPILOT_GITHUB_TOKEN` (recommended for `github-copilot/*`; workflow falls back to Actions `GITHUB_TOKEN`)
- Secret: `OPENAI_API_KEY` (only if chosen model/provider requires OpenAI API key auth)
- Optional secret: `FEATURE_FACTORY_CALLBACK_URL`
  - either full endpoint URL or base URL where `/api/integrations/execution-callback` can be appended
- Optional secret: `FEATURE_FACTORY_WEBHOOK_SECRET`
  - must match orchestrator `INTEGRATION_WEBHOOK_SECRET`
  - set together with callback URL (workflow now fails fast on partial callback secret config)

OpenClaw OAuth note:
- Local interactive terminals can use:
  - `openclaw onboard --auth-choice openai-codex`
  - `openclaw models auth login --provider openai-codex`
- GitHub Actions runners are non-interactive, so OAuth prompts are not available there.
- For CI runners, use non-interactive auth inputs (API keys/tokens) for the selected model provider.

## Callback integration (required for non-mock completion)

When `MOCK_MODE=false`, the orchestrator expects an external callback to update PR/preview status.

Configure in `.env`:

- `INTEGRATION_WEBHOOK_SECRET=...`
- `INTEGRATION_WEBHOOK_TTL_SECONDS=300`

Callback endpoint:

- `POST /api/integrations/execution-callback`

Required signed headers:

- `X-Feature-Factory-Timestamp`
- `X-Feature-Factory-Signature` (`sha256=<hmac-hex>`)
- `X-Feature-Factory-Event-Id` (idempotency key)

Signature input format:

- `<timestamp>.<raw_json_body>`

Payload should also include `event_id` to match the idempotency key for traceability.
