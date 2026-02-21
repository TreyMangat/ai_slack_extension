# Model Provider Strategy

## Current behavior

- Default (`CODERUNNER_MODE=opencode`):
  - `MOCK_MODE=true`: no model call, deterministic mock PR/preview output.
  - `MOCK_MODE=false` with `OPENCODE_EXECUTION_MODE=local_openclaw`:
    - worker executes OpenClaw directly inside container
    - worker commits/pushes branch and opens PR via GitHub App/token auth
  - `MOCK_MODE=false` with `OPENCODE_EXECUTION_MODE=github_issue_comment`:
    - worker posts OpenCode trigger issue comment
    - model execution happens in external runner/CI, which later calls:
      - `POST /api/integrations/execution-callback`
- Optional (`CODERUNNER_MODE=native_llm`):
  - worker calls model APIs directly inside the container.

## OpenClaw / OpenCode provider mode (recommended)

When `CODERUNNER_MODE=opencode`, choose one execution mode:

1. Local no-key mode (recommended for local POC):
   - `OPENCODE_EXECUTION_MODE=local_openclaw`
   - `OPENCLAW_AUTH_DIR=/home/app/.openclaw`
   - `OPENCLAW_AUTH_SEED_DIR=/run/secrets/openclaw`
   - sync host auth to `./secrets/openclaw` with `scripts/sync_openclaw_auth.ps1`
   - startup copies seed auth into writable runtime path inside container
   - optional deterministic pipeline check: `OPENCODE_DEBUG_BUILD=true` (writes `DEBUG_CODEGEN.md` and opens PR without model call)
   - UI requests automatically enforce frontend preview readiness:
     - detect UI keywords in intake/spec
     - require frontend build verification (`npm ci && npm run build`)
     - generate PR body instructions for Cloudflare Pages preview checks

2. Delegated CI mode:
   - `OPENCODE_EXECUTION_MODE=github_issue_comment`
   - external runner handles model execution and callbacks

To run delegated OpenCode in GitHub Actions without `OPENAI_API_KEY`:

1. Keep orchestrator in delegated mode:
   - `MOCK_MODE=false`
   - `CODERUNNER_MODE=opencode`
2. Use a GitHub-hosted model path in workflow:
   - `OPENCODE_MODEL=github-copilot/gpt-4.1` (default in this repo workflow)
3. Provide token auth in the runner env:
   - `COPILOT_GITHUB_TOKEN=<token>` (recommended)
   - fallback: Actions `GITHUB_TOKEN`
4. Ensure runner posts signed callbacks back to:
   - `POST /api/integrations/execution-callback`

To run with OpenCode + OpenAI API-key mode:

1. Keep orchestrator in delegated mode:
   - `MOCK_MODE=false`
   - `CODERUNNER_MODE=opencode`
2. Configure provider in the OpenCode/OpenClaw runtime (not in FastAPI):
   - `OPENAI_BASE_URL=https://api.openai.com/v1`
   - `OPENAI_API_KEY=<key or oauth-backed token in runner env>`
3. Ensure runner posts signed callbacks back to:
   - `POST /api/integrations/execution-callback`
4. Callback events for complete flow:
   - `pr_opened`
   - `preview_ready`
   - `build_failed`
   - `preview_failed`

OpenClaw auth options (from provider docs):

- API key mode:
  - `openclaw onboard --auth-choice openai-api-key`
  - `openclaw onboard --openai-api-key "$OPENAI_API_KEY"`
- Codex subscription OAuth mode:
  - `openclaw onboard --auth-choice openai-codex`
  - `openclaw models auth login --provider openai-codex`

Local helper scripts in this repo:
- `scripts/setup_openclaw.ps1`
- `scripts/check_openclaw_setup.ps1`

Important:
- OpenClaw OAuth is interactive and works on local terminals.
- GitHub Actions runners are non-interactive; use API-key/token auth there.

## Native LLM mode (experimental)

`CODERUNNER_MODE=native_llm` adds direct model execution inside the worker:

1. Clone target repo into isolated workspace.
2. Build deterministic optimized prompt from feature spec.
3. Request unified diff patch from configured model.
4. Apply patch, run tests, iterate up to `LLM_MAX_PATCH_ROUNDS`.
5. Commit, push branch, and open PR automatically.

Required config:
- `MOCK_MODE=false`
- `CODERUNNER_MODE=native_llm`
- `LLM_API_KEY`
- `GITHUB_AUTH_MODE=app` (recommended)

Current provider support in native mode:
- `LLM_PROVIDER=openai` (implemented)

## Prompt optimization in intake

The orchestrator now generates a deterministic `optimized_prompt` from each feature spec.

- No model call is used for this optimizer.
- It composes a cleaner build brief (objective, context, acceptance criteria, links, guardrails).
- The optimized prompt is stored in `spec.optimized_prompt` and included in GitHub issue context.

## Can we support multiple providers?

Yes. Recommended production pattern:

1. Keep this orchestrator as control-plane (intake, policy, approvals, audit).
2. Add a separate "runner gateway" service that supports provider routing:
   - OpenAI (GPT/Codex family)
   - Anthropic (Claude)
   - Google (Gemini)
3. Store provider API keys in a secrets manager.
4. Enforce per-provider allowlists and budget limits.
5. Report deterministic status back via signed callback.

## Low-cost / "free tier" note

Provider free tiers are usually rate-limited and may be unsuitable for org-grade reliability.
Use free tiers only for local experimentation; keep production on paid, quota-controlled keys.
