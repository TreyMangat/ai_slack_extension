# Code Factory PR Loop

This repo now enforces a deterministic PR loop:

1. Agent writes code on a branch.
2. `preflight` computes risk tier from changed paths using `.github/risk-policy.yml`.
3. Policy violations block early (for example, control-plane changes without docs updates).
4. Required checks run based on risk tier:
   - `lint-static`
   - `unit-tests`
   - `db-migrations`
   - `api-smoke`
   - `security-codeql`
   - `ui-evidence` (conditional by path)
5. `review-agent` runs as a required fallback review check.
6. `head-sha-gate` verifies required checks are green on the PR head SHA.
7. Merge remains blocked until branch protection required checks pass.

## Source of truth

- Policy contract: `.github/risk-policy.yml`
- Gate logic: `.github/scripts/risk_policy_gate.py`

Edit the policy file to tune:
- risk tier path mapping,
- required checks by tier,
- docs drift rules,
- UI evidence requirements.

## Risk tiers and required checks

Defined in `.github/risk-policy.yml`:

- `low`: docs/metadata only -> `lint-static`, `unit-tests`
- `medium`: scripts/container/dependency changes -> `lint-static`, `unit-tests`, `db-migrations`, `api-smoke`
- `high`: application behavior changes -> `lint-static`, `unit-tests`, `db-migrations`, `api-smoke`, `security-codeql`
- `critical`: security/integration/control-plane changes -> `lint-static`, `unit-tests`, `db-migrations`, `api-smoke`, `security-codeql`
- Conditional: `ui-evidence` when UI paths change

## Gate failure interpretation

Common failures:

- `preflight` failed:
  - docs drift violation: control-plane changed without docs update.
  - invalid policy/script execution state.
- `api-smoke` failed:
  - runtime behavior regressed (health/spec/build flow broken).
- `ui-evidence` failed:
  - required UI manifest missing/invalid, required flow missing, or entrypoint mismatch.
- `review-agent` failed:
  - one or more required checks for the tier are not green.
- `head-sha-gate` failed:
  - required checks are missing/failing on current head SHA (stale signal protection).

## UI evidence model

When UI paths change, CI requires:

- manifest file: `artifacts/ui-evidence-manifest.json`,
- required flows from policy:
  - `home_page_renders`
  - `feature_detail_renders`
  - `create_request_then_open_feature_ui`
- entrypoint and structural validation via `.github/scripts/validate_ui_evidence.py`.

To add future UI flows:

1. Extend `ui_evidence.required_flows` in `.github/risk-policy.yml`.
2. Extend `scripts/ui_evidence.ps1` to execute and assert those flows.
3. Keep validation deterministic (no random waits or flaky selectors).

## Review agent integration

Current mode is fallback-only:

- `review-agent` uses deterministic check results (lint/smoke/codeql/UI evidence).
- Findings artifact: `review-agent-findings`.

To swap in a third-party reviewer later:

1. Keep check name `review-agent`.
2. Replace logic in `.github/scripts/review_agent_fallback.py` or workflow step with service output parsing.
3. Keep `head-sha-gate` verification unchanged so stale external summaries cannot pass.

## Canonical rerun strategy

This repo intentionally avoids comment-triggered bot reruns to prevent races.

Use one canonical rerun path:

- Re-run the `risk-policy-gate` workflow from Actions UI, or
- run `workflow_dispatch` with `expected_head_sha`.

The workflow has:

- per-PR concurrency with `cancel-in-progress: true`,
- optional SHA guard for deterministic reruns.

## Harness gap loop

Use harness artifacts to convert findings into regressions:

- docs: `tests/harness/README.md`
- tracking issue template: `.github/ISSUE_TEMPLATE/harness-gap.yml`

## Required human setup

Automation does not set branch protection automatically. Configure this in GitHub:

1. Protect default branch.
2. Require pull request before merge.
3. Require status checks:
   - `preflight`
   - `review-agent`
   - `head-sha-gate`
4. Optionally require:
   - `lint-static`
   - `unit-tests`
   - `api-smoke`
   - `security-codeql`
   - `ui-evidence`
5. Require branches to be up to date before merging.
6. Restrict who can dismiss reviews and who can push to protected branch.
