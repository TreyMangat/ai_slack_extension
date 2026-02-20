# GitHub setup

## Minimal local testing (no OpenCode)

You can enable GitHub issue creation without OpenCode.

1. Create a Personal Access Token (PAT)
   - For private repos: `repo` scope
   - For public repos: `public_repo`

2. In `.env` set:

- `GITHUB_ENABLED=true`
- `GITHUB_TOKEN=...`
- `GITHUB_REPO_OWNER=...`
- `GITHUB_REPO_NAME=...`

3. Restart docker compose.

## With OpenCode

This scaffold triggers OpenCode by posting a comment to the created issue.
It also includes build context (implementation mode + referenced repos) so your runner can choose safe clone/copy workflows.

For reuse mode, the orchestrator also prepares local workspace snapshots and writes a manifest:
- `WORKSPACE_ROOT=/tmp/feature_factory_workspaces`
- `WORKSPACE_ENABLE_GIT_CLONE=false` by default (recommended until reviewed)
- `WORKSPACE_LOCAL_COPY_ROOT=/app` restricts local snapshot scope

You must install OpenCode in the repo first (typically as a GitHub Action).
Then set the trigger comment:

- `OPENCODE_TRIGGER_COMMENT=/oc Implement this issue. Follow acceptance criteria. Add tests.`

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

Signature input format:

- `<timestamp>.<raw_json_body>`
