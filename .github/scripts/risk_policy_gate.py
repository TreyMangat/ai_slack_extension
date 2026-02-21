#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_TIER_ORDER = ["low", "medium", "high", "critical"]


@dataclass
class GateResult:
    risk_tier: str
    changed_files: list[str]
    required_checks: list[str]
    ui_required: bool
    docs_drift_ok: bool
    control_plane_changed: list[str]
    docs_changed: list[str]
    violations: list[str]

    @property
    def blocked(self) -> bool:
        return bool(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_tier": self.risk_tier,
            "changed_files": self.changed_files,
            "changed_files_count": len(self.changed_files),
            "required_checks": self.required_checks,
            "ui_required": self.ui_required,
            "docs_drift_ok": self.docs_drift_ok,
            "control_plane_changed": self.control_plane_changed,
            "docs_changed": self.docs_changed,
            "violations": self.violations,
            "blocked": self.blocked,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Risk policy preflight gate")
    parser.add_argument("--policy", required=True, help="Path to .github/risk-policy.yml")
    parser.add_argument("--base-sha", default="", help="Base commit SHA")
    parser.add_argument("--head-sha", default="", help="Head commit SHA")
    parser.add_argument("--event-name", default="", help="GitHub event name")
    parser.add_argument("--event-path", default="", help="GitHub event payload path")
    parser.add_argument(
        "--changed-files-file",
        default="",
        help="Optional file with newline-delimited changed file paths",
    )
    parser.add_argument("--output-json", required=True, help="Output JSON path")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    return parser.parse_args()


def _load_policy(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Risk policy contract must be a mapping.")
    return data


def _is_zero_sha(value: str) -> bool:
    value = value.strip()
    return bool(value) and set(value) == {"0"}


def _git_changed_files(base_sha: str, head_sha: str) -> list[str]:
    if not head_sha:
        head_sha = "HEAD"
    if _is_zero_sha(base_sha):
        base_sha = ""

    if base_sha:
        rev_range = f"{base_sha}..{head_sha}"
    else:
        # Local fallback for workflow_dispatch and first push.
        rev_range = f"{head_sha}~1..{head_sha}"

    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", rev_range]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        # Final fallback for empty history / shallow edge cases.
        proc = subprocess.run(
            ["git", "ls-files"],
            check=False,
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Unable to resolve changed files.")
    files = [line.strip().lstrip("\ufeff").replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    return sorted(set(files))


def _file_matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _choose_risk_tier(changed_files: list[str], policy: dict[str, Any]) -> str:
    tier_order = policy.get("tier_order", DEFAULT_TIER_ORDER)
    risk_tiers = policy.get("risk_tiers", {})
    default_tier = policy.get("defaults", {}).get("risk_tier", tier_order[0])

    highest_idx = tier_order.index(default_tier) if default_tier in tier_order else 0
    for idx, tier in enumerate(tier_order):
        tier_cfg = risk_tiers.get(tier, {})
        globs = tier_cfg.get("globs", [])
        if any(_file_matches(path, globs) for path in changed_files):
            highest_idx = max(highest_idx, idx)
    return tier_order[highest_idx]


def _required_checks_for_changes(
    risk_tier: str,
    changed_files: list[str],
    policy: dict[str, Any],
) -> tuple[list[str], bool]:
    checks = list(policy.get("required_checks_by_tier", {}).get(risk_tier, []))

    ui_cfg = policy.get("ui_evidence", {})
    ui_required = bool(ui_cfg.get("enabled", False)) and any(
        _file_matches(path, ui_cfg.get("trigger_globs", [])) for path in changed_files
    )

    for conditional in policy.get("conditional_required_checks", []):
        check_name = conditional.get("check", "").strip()
        globs = conditional.get("when_any_globs", [])
        if not check_name:
            continue
        if any(_file_matches(path, globs) for path in changed_files):
            checks.append(check_name)

    # De-dupe while preserving order.
    deduped_checks = list(dict.fromkeys(checks))
    return deduped_checks, ui_required


def _evaluate_docs_drift(changed_files: list[str], policy: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    docs_cfg = policy.get("docs_drift", {})
    control_globs = docs_cfg.get("control_plane_globs", [])
    docs_globs = docs_cfg.get("required_docs_globs", [])

    control_plane_changed = [path for path in changed_files if _file_matches(path, control_globs)]
    docs_changed = [path for path in changed_files if _file_matches(path, docs_globs)]

    docs_ok = (not control_plane_changed) or bool(docs_changed)
    return docs_ok, control_plane_changed, docs_changed


def _write_json(path: str, payload: dict[str, Any]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_github_outputs(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    output_lines = [
        f"risk_tier={payload['risk_tier']}",
        f"required_checks_json={json.dumps(payload['required_checks'])}",
        f"ui_required={str(payload['ui_required']).lower()}",
        f"docs_drift_ok={str(payload['docs_drift_ok']).lower()}",
        f"blocked={str(payload['blocked']).lower()}",
        f"head_sha={payload.get('head_sha', '')}",
        f"base_sha={payload.get('base_sha', '')}",
    ]
    with open(path, "a", encoding="utf-8") as f:
        for line in output_lines:
            f.write(f"{line}\n")


def _write_step_summary(result: GateResult, event_name: str, base_sha: str, head_sha: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    status = "BLOCKED" if result.blocked else "PASS"
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Risk Policy Preflight\n")
        f.write(f"- Event: `{event_name or 'unknown'}`\n")
        f.write(f"- Base SHA: `{base_sha or 'n/a'}`\n")
        f.write(f"- Head SHA: `{head_sha or 'n/a'}`\n")
        f.write(f"- Risk tier: `{result.risk_tier}`\n")
        f.write(f"- Required checks: `{', '.join(result.required_checks) or 'none'}`\n")
        f.write(f"- UI evidence required: `{str(result.ui_required).lower()}`\n")
        f.write(f"- Docs drift rule: `{'pass' if result.docs_drift_ok else 'fail'}`\n")
        f.write(f"- Gate status: `{status}`\n")
        if result.violations:
            f.write("### Violations\n")
            for violation in result.violations:
                f.write(f"- {violation}\n")
        f.write("### Changed Files\n")
        if result.changed_files:
            for path in result.changed_files:
                f.write(f"- `{path}`\n")
        else:
            f.write("- (none)\n")


def main() -> int:
    args = _parse_args()
    policy = _load_policy(args.policy)

    if args.changed_files_file:
        changed_files = [
            line.strip().lstrip("\ufeff").replace("\\", "/")
            for line in Path(args.changed_files_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        changed_files = _git_changed_files(args.base_sha, args.head_sha)

    risk_tier = _choose_risk_tier(changed_files, policy)
    required_checks, ui_required = _required_checks_for_changes(risk_tier, changed_files, policy)
    docs_drift_ok, control_plane_changed, docs_changed = _evaluate_docs_drift(changed_files, policy)

    violations: list[str] = []
    if not docs_drift_ok:
        violations.append(
            "Control-plane files changed without a docs update. Update docs/**, README.md, or docs/POLICY.md."
        )

    result = GateResult(
        risk_tier=risk_tier,
        changed_files=changed_files,
        required_checks=required_checks,
        ui_required=ui_required,
        docs_drift_ok=docs_drift_ok,
        control_plane_changed=control_plane_changed,
        docs_changed=docs_changed,
        violations=violations,
    )

    payload = result.to_dict()
    payload["base_sha"] = args.base_sha
    payload["head_sha"] = args.head_sha
    payload["event_name"] = args.event_name

    _write_json(args.output_json, payload)
    _write_github_outputs(args.github_output, payload)
    _write_step_summary(result, args.event_name, args.base_sha, args.head_sha)

    if result.blocked:
        print("Risk policy gate blocked:")
        for violation in result.violations:
            print(f"- {violation}")
        return 1

    print(f"Risk policy gate passed with tier={result.risk_tier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
