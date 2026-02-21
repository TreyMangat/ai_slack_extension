# Model Provider Strategy

## Current behavior

- Default (`CODERUNNER_MODE=opencode`):
  - `MOCK_MODE=true`: no model call, deterministic mock PR/preview output.
  - `MOCK_MODE=false`: creates a GitHub issue and posts an OpenCode trigger comment.
  - model execution happens in your external runner/CI, which later calls:
    - `POST /api/integrations/execution-callback`
- Optional (`CODERUNNER_MODE=native_llm`):
  - worker calls model APIs directly inside the container.

## OpenClaw / OpenCode provider mode (recommended)

When `CODERUNNER_MODE=opencode`, this orchestrator delegates coding to your external OpenCode runner.

To run with OpenClaw + OpenAI provider:

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
