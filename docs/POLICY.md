# Policy (Merge + Risk)

This scaffold intentionally **does not auto-merge** by default.

## Why

User approval in Slack/UI is **product approval**, not engineering approval.

In production you should require:
- CI checks passing
- CODEOWNERS approvals
- security scans
- explicit human review for risky changes

## Config

- `DISABLE_AUTOMERGE=true` blocks auto-merge even when everything is green.

Future improvements:
- risk scoring by touched paths
- detection of migrations/auth/payments
- feature-flag requirement for risky areas

