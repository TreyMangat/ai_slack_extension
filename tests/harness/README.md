# Harness Cases

This folder stores repeatable regression cases created from production findings, review-agent findings, or incidents.

## Why this exists

The policy gate is only useful long-term if recurring failures get converted into deterministic tests. Every notable finding should end in a harness case.

## How to add a harness case

1. Create a new folder under `tests/harness/cases/` with a stable slug.
2. Add a `case.md` containing:
   - trigger (what changed or failed),
   - expected behavior,
   - minimal reproduction steps,
   - assertion(s) that should pass after the fix.
3. Add or update automated checks where possible:
   - API flow: extend `scripts/smoke_test.ps1`.
   - UI flow: extend `scripts/ui_evidence.ps1`.
   - Static/guardrail issue: extend `.github/scripts/*`.
4. Reference the harness case in the PR description and any incident ticket.

## Case template

Use this minimal template:

```text
Title:
Date:
Source:
Risk tier:

Trigger:
Expected:
Repro:
Assertions:
```
