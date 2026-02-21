# OpenClaw Auth In Docker

This project supports OpenAI Codex OAuth via OpenClaw **without API keys** for local POC runs.

## Where auth lives

- Host machine auth is created by OpenClaw under:
  - Windows: `%USERPROFILE%\.openclaw`
- Runtime containers use:
  - seed path (read-only): `/run/secrets/openclaw`
  - runtime path (writable): `/home/app/.openclaw`

## How containers get auth

1. Sync host auth into repo secrets mount:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_openclaw_auth.ps1
```

2. Docker Compose already mounts repo secrets:
   - `./secrets:/run/secrets:ro`
   - so OpenClaw seed path is `/run/secrets/openclaw`

3. On startup, app services copy auth from seed path into writable runtime path:
   - `/home/app/.openclaw`
   - this avoids Windows bind-mount permission issues (`chmod`/`EPERM`).
   - if `openclaw.json` contains a Windows workspace path, runtime normalizes it to `/tmp/openclaw_workspace`.

4. Startup fails fast when auth is missing for local OpenClaw mode:
   - `CODERUNNER_MODE=opencode`
   - `OPENCODE_EXECUTION_MODE=local_openclaw`

## Verify end-to-end auth wiring

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_openclaw_setup.ps1 -CheckContainer
```

This validates both:
- host OpenClaw auth
- worker-container OpenClaw auth

## Security notes

- `./secrets/openclaw` must stay gitignored.
- Rotate/re-login OpenClaw auth if tokens are exposed.
- Use GitHub App auth for repo operations (`GITHUB_AUTH_MODE=app`) to minimize blast radius.
