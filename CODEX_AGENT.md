# CODEX_AGENT.md — PRFactory

## ROLE
You are the execution agent for PRFactory. You own tests, Docker/compose, scripts, worker tasks, templates, static assets, alembic migrations, CI/CD, and deployment. You do NOT own the service layer, API routes, adapters, or state machine — those belong to Claude Code.

## FILE OWNERSHIP (DO NOT TOUCH CLAUDE CODE FILES)
You own:
- `orchestrator/app/tasks/**` (RQ worker task wrappers)
- `orchestrator/app/templates/**` (Jinja HTML)
- `orchestrator/app/static/**` (CSS, JS, assets)
- `orchestrator/Dockerfile`
- `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.indexer.yml`
- `scripts/**` (run_local.ps1, smoke_test.ps1, migrate.ps1, deploy_modal_prod.ps1)
- `tests/**` (ALL test files — unit, integration, e2e)
- `.env.example`
- `modal_app.py`
- `alembic/**` (migrations)
- `.github/workflows/` (if present)
- `Makefile` (if present)

You do NOT touch:
- `orchestrator/app/services/` (Claude Code)
- `orchestrator/app/api/` (Claude Code)
- `orchestrator/app/models/` (Claude Code)
- Adapter implementations (Claude Code)
- State machine logic (Claude Code)

---

## PROMPT 1: DISCOVERY AUDIT (DO THIS FIRST)

I need a comprehensive infrastructure and test audit of the PRFactory codebase. Please do the following and report back with ALL findings:

### 1. Full file tree
Run `find . -type f \( -name "*.py" -o -name "*.yml" -o -name "*.yaml" -o -name "*.ps1" -o -name "*.sh" -o -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "Dockerfile" -o -name ".env*" \) | head -300` from the repo root. Give me the complete listing.

### 2. Docker and compose audit
For each compose file (docker-compose.yml, docker-compose.dev.yml, docker-compose.indexer.yml):
- List every service defined
- Show port mappings
- Show volume mounts
- Show environment variable passthrough
- Show profile assignments (especially the slackbot profile)
- Show depends_on chains
- Show healthcheck configurations

For the Dockerfile:
- Base image
- Build stages
- Entrypoint/CMD
- How does it differentiate between api, worker, cleanup, and slackbot processes?

### 3. Worker tasks audit
For each file in `orchestrator/app/tasks/`:
- What task functions are defined?
- What RQ queue are they enqueued to?
- What service methods do they call?
- Error handling: what happens when a task fails? Retry? Dead letter? State update?
- Timeout configuration?
- How are tasks enqueued? (from API handlers? from other tasks? from Slack?)

### 4. Test coverage audit
Run:
```bash
find tests/ -name "*.py" -type f
```
Then for each test file:
- What module/feature does it test?
- How many test functions?
- Are fixtures shared or per-file?
- Do tests use the mock adapter or real services?

Run the full test suite and report:
```bash
py -3.12 -m pytest -q --tb=short 2>&1
```

Then check coverage if possible:
```bash
py -3.12 -m pytest --cov=orchestrator --cov-report=term-missing -q 2>&1 | tail -40
```

Identify the biggest coverage gaps — especially around:
- Slack bot event handling
- State machine transitions
- Worker task error paths
- Adapter mock/real switching
- Auth/RBAC enforcement

### 5. Scripts audit
For each script in `scripts/`:
- What does it do?
- Does it work on Windows/PowerShell?
- Any hardcoded paths or assumptions?
- Does smoke_test.ps1 actually validate the Slack bot?

### 6. Alembic/migrations audit
- What's the current migration head?
- How many migrations exist?
- Do they match the current model definitions?
- Is there a clean path from empty DB to current schema?
Run:
```bash
cat alembic/versions/*.py 2>/dev/null | head -100
```
Or equivalent to see migration history.

### 7. Templates and static assets
- List all HTML templates
- What template engine? (Jinja2?)
- Are templates using a consistent layout/base?
- Static assets: any JS that talks to the API? WebSocket connections?
- Is there a UI for the full feature request lifecycle?

### 8. Environment and config
Show the full `.env.example` contents. For each variable, note:
- Is it required or optional?
- What's the default?
- Is it documented?

### 9. Search for infrastructure problems
```bash
grep -rn "TODO\|FIXME\|HACK\|XXX" tests/ scripts/ orchestrator/app/tasks/ orchestrator/app/templates/ --include="*.py" --include="*.ps1" --include="*.html"
grep -rn "localhost\|127.0.0.1" docker-compose*.yml orchestrator/Dockerfile
grep -rn "sleep\|time.sleep" orchestrator/ --include="*.py"  # potential race conditions
```

### 10. Slack bot Docker integration
- How is the slackbot profile activated in docker-compose?
- What happens if Socket Mode credentials are missing?
- Does the slackbot container share code with the API container?
- Can slackbot and API run in the same container or must they be separate?
- Is there a healthcheck for the slackbot process?

### FORMAT YOUR RESPONSE AS:
```
## FILE TREE
[full listing]

## DOCKER / COMPOSE
[findings per compose file and Dockerfile]

## WORKER TASKS
[findings per task file]

## TEST COVERAGE
[test suite results, coverage gaps]

## SCRIPTS
[findings per script]

## MIGRATIONS
[findings]

## TEMPLATES / STATIC
[findings]

## ENVIRONMENT
[.env.example contents with analysis]

## INFRASTRUCTURE PROBLEMS
[every TODO, race condition, hardcoded value]

## TOP 10 CONCERNS (ranked by severity)
[your assessment of the biggest infrastructure/test problems]
```

---

## ONGOING WORKFLOW RULES

After the discovery audit, follow these rules for all subsequent prompts:

1. **Test everything.** After every change, run `py -3.12 -m pytest -q` and report results.
2. **Docker builds must pass.** After any Dockerfile or compose change, run `docker compose -f docker-compose.yml -f docker-compose.dev.yml config` to validate.
3. **Write tests for new behavior.** Every bug fix or feature should have a corresponding test.
4. **Test the Slack bot paths.** Write tests that verify: event parsing, message construction, threading, error handling — using mock adapters.
5. **Scripts must work on Windows/PowerShell.** No bash-only scripts.
6. **Migrations must be reversible.** Every alembic migration needs a downgrade path.
7. **Don't break the smoke test.** After changes, run `.\scripts\smoke_test.ps1` and verify it passes.
8. **Report what you changed.** After every edit session, list: files modified, tests added/changed, Docker changes, and full test suite results.
9. **Coverage matters.** Track which areas of the codebase have no test coverage and flag them.
