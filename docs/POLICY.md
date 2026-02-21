# Policy (Merge + Risk)

This project enforces merge policy through a machine-readable contract:

- `.github/risk-policy.yml`

The preflight gate uses that file to compute risk tier from changed paths and enforce deterministic rules before expensive checks run.

## Current policy model

- Risk tiers by changed paths: `low`, `medium`, `high`, `critical`
- Required checks per tier
- Unit test gate on all risk tiers
- Docs drift enforcement for control-plane changes
- Conditional UI evidence checks for UI-related paths
- Head SHA freshness gate to reject stale check signals

## Merge principles

User approval in Slack/UI is product approval only. Merge readiness still requires engineering/security gates.

Defaults:

- `DISABLE_AUTOMERGE=true` (auto-merge off)
- required CI checks before merge (configure in branch protection)

See:

- `docs/code-factory.md` for full PR loop operations
- `.github/workflows/risk-policy-gate.yml` for enforced CI order
