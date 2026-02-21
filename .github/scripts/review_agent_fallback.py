#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any


PASS_RESULTS = {"success"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fallback review-agent check")
    parser.add_argument("--required-checks-json", required=True, help="JSON array of required check names")
    parser.add_argument("--check-results-json", required=True, help="JSON mapping of check->job result")
    parser.add_argument("--risk-tier", required=True, help="Computed risk tier")
    parser.add_argument("--head-sha", required=True, help="Current head commit SHA")
    parser.add_argument("--output", required=True, help="Path to findings JSON output")
    return parser.parse_args()


def _write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Review Agent (Fallback)\n")
        for line in lines:
            f.write(f"- {line}\n")


def _load_json_arg(raw: str) -> Any:
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8-sig"))
    return json.loads(raw)


def main() -> int:
    args = _parse_args()
    required_checks = _load_json_arg(args.required_checks_json)
    check_results = _load_json_arg(args.check_results_json)

    findings: list[dict[str, Any]] = []
    for check_name in required_checks:
        result = check_results.get(check_name, "missing")
        if result not in PASS_RESULTS:
            findings.append(
                {
                    "severity": "high",
                    "check": check_name,
                    "result": result,
                    "message": f"Required check '{check_name}' is not green on current run.",
                }
            )

    output = {
        "schema_version": 1,
        "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "head_sha": args.head_sha,
        "risk_tier": args.risk_tier,
        "required_checks": required_checks,
        "check_results": check_results,
        "findings": findings,
        "status": "fail" if findings else "pass",
        "review_mode": "fallback",
        "notes": [
            "No external review bot is configured in this repository.",
            "Fallback review-agent uses deterministic CI checks and policy evaluation.",
        ],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    summary = [
        f"Risk tier: `{args.risk_tier}`",
        f"Head SHA: `{args.head_sha}`",
        f"Required checks: `{', '.join(required_checks) or 'none'}`",
        f"Findings: `{len(findings)}`",
        f"Status: `{output['status']}`",
    ]
    _write_summary(summary)

    if findings:
        for finding in findings:
            print(f"{finding['check']}: {finding['message']} (result={finding['result']})")
        return 1

    print("Fallback review-agent check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
