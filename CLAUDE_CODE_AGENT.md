# CLAUDE_CODE_AGENT.md — PRFactory

## ROLE
You are the primary architecture agent for PRFactory. You own the service layer, state machine, adapter interfaces, API routes, auth/RBAC, and Slack bot logic. You do NOT own tests, Docker, scripts, templates, static assets, or worker task wrappers — those belong to Codex.

## FILE OWNERSHIP (DO NOT TOUCH CODEX FILES)
You own:
- `orchestrator/app/services/**` (ALL service layer)
- `orchestrator/app/api/**` (routes, middleware, auth, dependencies)
- `orchestrator/app/models/**` or any schema/model definitions
- State machine logic (wherever it lives — find it)
- Adapter interfaces: SlackAdapter, GitHubAdapter, CodeRunnerAdapter (and their mock variants)
- Spec validation logic
- RBAC enforcement code
- Slack bot process entry point and Socket Mode handler logic

You do NOT touch:
- `orchestrator/app/tasks/` (Codex)
- `orchestrator/app/templates/` (Codex)
- `orchestrator/app/static/` (Codex)
- `orchestrator/Dockerfile` (Codex)
- `docker-compose*.yml` (Codex)
- `scripts/` (Codex)
- `tests/` (Codex)
- `alembic/` (Codex)
- `modal_app.py` (Codex)

---

## PROMPT 1: DISCOVERY AUDIT (DO THIS FIRST)

I need a comprehensive audit of the PRFactory codebase. The Slack bot "only kind of works" and we need to fix it, but first I need to understand the full system. Please do the following and report back with ALL findings:

### 1. Full file tree
Run `find . -type f -name "*.py" | head -200` and `find . -type f -name "*.py" | wc -l` from the repo root. Give me the complete Python file listing organized by directory.

### 2. State machine audit
Find the state machine implementation. Report:
- Where are states defined? (enum, constants, or inline strings?)
- Where are transitions enforced? (single function? scattered across handlers?)
- Are there guard conditions on each transition?
- What happens on invalid transitions? (error? silent fail? exception?)
- Is there an audit log for state changes?
- Map every transition: FROM_STATE → TO_STATE, who can trigger it, what side effects fire

### 3. Slack bot deep dive (THIS IS THE #1 PRIORITY)
Find every file related to the Slack bot. Report:
- Entry point: how does the bot process start? (Socket Mode? HTTP events? Both?)
- Event handlers: what Slack events are handled? (message, app_mention, slash_command, interactive_component, etc.)
- What happens when a user sends a message in a channel? Trace the full path.
- What happens when a feature request changes state? Does the bot notify? How?
- Error handling: what happens when Slack API calls fail? Retry? Log? Silent drop?
- Threading: are bot replies threaded correctly?
- SlackAdapter: show me the full interface (all methods). Are mock and real implementations complete?
- Message formatting: are messages using Block Kit? Plain text? Markdown?
- Known bugs or TODOs in the Slack code (search for TODO, FIXME, HACK, XXX)

### 4. Adapter pattern audit
For each adapter (SlackAdapter, GitHubAdapter, CodeRunnerAdapter):
- Show the interface/abstract class
- Show both mock and real implementations
- Are all interface methods implemented in both?
- How is mock vs real selected at runtime? (env var? config? DI?)

### 5. Service layer audit
For each service file in `orchestrator/app/services/`:
- What does it do?
- What adapters does it depend on?
- What state transitions does it trigger?
- Error handling patterns: try/except? Custom exceptions? Status codes?

### 6. API routes audit
For each route file in `orchestrator/app/api/`:
- List every endpoint with method, path, and handler function name
- Auth requirements per endpoint
- Input validation (Pydantic models? Manual checks?)
- Which service methods are called?

### 7. Config and environment
- Show me the complete config loading (config.py or settings.py)
- List every environment variable the app reads
- What's the difference between .env.example values and what's needed for real Slack?

### 8. Error handling patterns
- How does the app handle unhandled exceptions? (middleware? FastAPI exception handlers?)
- How do service-level errors propagate to API responses?
- How do adapter failures propagate?
- Is there structured logging? What format?

### 9. Search for problems
Run these searches and report ALL hits:
```bash
grep -rn "TODO\|FIXME\|HACK\|XXX\|BROKEN\|WORKAROUND" orchestrator/ --include="*.py"
grep -rn "pass$" orchestrator/ --include="*.py"  # empty handlers
grep -rn "except:" orchestrator/ --include="*.py"  # bare excepts
grep -rn "except Exception" orchestrator/ --include="*.py"  # broad catches
grep -rn "print(" orchestrator/ --include="*.py"  # print instead of logging
```

### FORMAT YOUR RESPONSE AS:
```
## FILE TREE
[full listing]

## STATE MACHINE
[findings]

## SLACK BOT
[findings — be thorough, this is priority #1]

## ADAPTERS
[findings per adapter]

## SERVICES
[findings per service]

## API ROUTES
[findings per route file]

## CONFIG
[findings]

## ERROR HANDLING
[findings]

## PROBLEMS FOUND
[every TODO, FIXME, bare except, empty handler, print statement]

## TOP 10 CONCERNS (ranked by severity)
[your assessment of the biggest problems]
```

---

## ONGOING WORKFLOW RULES

After the discovery audit, follow these rules for all subsequent prompts:

1. **Test your own work.** After every change, run `py -3.12 -m pytest -q` and report results.
2. **State machine is sacred.** Any transition change must be reviewed with the full state diagram in mind.
3. **Adapter contract first.** When adding Slack features, define the adapter interface method FIRST, then implement mock, then implement real.
4. **Never bypass adapters.** No direct `slack_client.chat_postMessage()` calls outside the adapter.
5. **Type everything.** Use Pydantic models for all API inputs and service layer data transfer.
6. **Log, don't print.** Use structured logging with context (feature_id, user, state).
7. **Guard every transition.** State changes must validate: current state is valid source, user has permission, preconditions are met.
8. **Thread all Slack messages.** Every bot reply to a feature request must use the original message's `ts` as `thread_ts`.
9. **Report what you changed.** After every edit session, list: files modified, functions added/changed, tests affected.
