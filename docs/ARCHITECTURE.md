# Architecture

## High-level

This scaffold separates the system into four concerns:

1. **Intake** (Slack/UI) -> produces a *validated spec*
2. **Orchestration** -> state machine + persistence + audit logs
3. **Execution** (Workspace prep + Code Runner) -> prepares isolated snapshots, creates PR + preview
4. **Governance** -> approvals + merge gates

## Why adapters

Everything that touches an external system is behind an adapter interface:

- SlackAdapter
- GitHubAdapter
- CodeRunnerAdapter
- RepoIndexerAdapter (HTTP; optional)

This lets you:
- run locally in MOCK_MODE
- swap implementations later
- unit test orchestration separately

## State machine

`NEW` -> `NEEDS_INFO` -> `READY_FOR_BUILD` -> `BUILDING` -> `PR_OPENED` -> `PREVIEW_READY` -> `PRODUCT_APPROVED` -> `READY_TO_MERGE` -> `MERGED`

Failure states:
- `FAILED_SPEC`
- `FAILED_BUILD`
- `FAILED_PREVIEW`
- `NEEDS_HUMAN`

## Security posture

- Intake is treated as **untrusted**
- External tool access is minimized
- Credentials should be least privilege

For production:
- run execution in isolated runners (CI)
- store secrets in a secrets manager
- require CODEOWNERS + branch protection
- enforce workspace cleanup and snapshot quotas


