# Model Provider Strategy

## Current behavior

- Default (`CODERUNNER_MODE=opencode`):
  - `MOCK_MODE=true`: deterministic mock PR/preview output.
  - `MOCK_MODE=false` + `OPENCODE_EXECUTION_MODE=local_openclaw`:
    - worker executes OpenClaw directly in-container
    - worker commits/pushes branch and opens PR
- Optional (`CODERUNNER_MODE=native_llm`):
  - worker calls model APIs directly in-container and opens PR

Issue-comment delegated mode was removed from runtime flow.

## OpenClaw mode (recommended for local no-key flow)

Set:
- `CODERUNNER_MODE=opencode`
- `OPENCODE_EXECUTION_MODE=local_openclaw`
- `OPENCLAW_AUTH_DIR=/home/app/.openclaw`
- `OPENCLAW_AUTH_SEED_DIR=/run/secrets/openclaw`

Then:
- sync host auth to `./secrets/openclaw` with `scripts/sync_openclaw_auth.ps1`
- startup stages seed auth into runtime path
- runner opens PR directly

Optional deterministic pipeline probe:
- `OPENCODE_DEBUG_BUILD=true` (writes `DEBUG_CODEGEN.md` and still opens PR)

## Native LLM mode (experimental)

Set:
- `MOCK_MODE=false`
- `CODERUNNER_MODE=native_llm`
- `LLM_API_KEY`

Flow:
1. Clone target repo in isolated workspace.
2. Build deterministic optimized prompt from feature spec.
3. Request patch from configured provider.
4. Apply patch, run tests, iterate up to `LLM_MAX_PATCH_ROUNDS`.
5. Commit, push, open PR.

## Prompt optimization in intake

- deterministic prompt builder (no model call) stores `spec.optimized_prompt`
- used by runners to produce consistent implementation context

## Multi-provider recommendation

For production scale:
1. Keep orchestrator as control plane.
2. Add runner gateway with provider routing (OpenAI/Claude/Gemini).
3. Keep provider keys in secrets manager.
4. Enforce model allowlists + budget limits.
5. Send signed status callbacks to `/api/integrations/execution-callback`.
