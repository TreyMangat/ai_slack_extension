#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


SUCCESS_CONCLUSIONS = {"success"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify required checks exist on current head SHA")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--token", required=True, help="GitHub token")
    parser.add_argument("--head-sha", required=True, help="Current PR head SHA")
    parser.add_argument("--required-checks-json", required=True, help="JSON list from preflight")
    parser.add_argument("--must-include", action="append", default=[], help="Additional required checks")
    parser.add_argument("--exclude-check", action="append", default=[], help="Checks to exclude")
    parser.add_argument("--retries", type=int, default=10, help="Retry attempts for eventual consistency")
    parser.add_argument("--retry-delay-seconds", type=float, default=3.0, help="Delay between retries")
    return parser.parse_args()


def _api_get(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "feature-factory-head-sha-gate",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _latest_conclusions(check_runs: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    # name -> (status, conclusion)
    latest: dict[str, tuple[str, str, str]] = {}
    for run in check_runs:
        name = run.get("name", "")
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        completed_at = run.get("completed_at") or ""
        previous = latest.get(name)
        if previous is None or completed_at > previous[2]:
            latest[name] = (status, conclusion, completed_at)
    return {name: (status, conclusion) for name, (status, conclusion, _) in latest.items()}


def _write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## Head SHA Gate\n")
        for line in lines:
            f.write(f"- {line}\n")


def main() -> int:
    args = _parse_args()
    required_checks = set(json.loads(args.required_checks_json))
    required_checks.update(args.must_include)
    required_checks.difference_update(args.exclude_check)

    if not required_checks:
        print("No required checks to verify.")
        return 0

    failures: list[str] = []
    check_state: dict[str, tuple[str, str]] = {}

    url = f"https://api.github.com/repos/{args.repo}/commits/{args.head_sha}/check-runs?per_page=100"
    for attempt in range(1, args.retries + 1):
        try:
            payload = _api_get(url, args.token)
            check_runs = payload.get("check_runs", [])
            check_state = _latest_conclusions(check_runs)
        except urllib.error.HTTPError as err:
            failures = [f"GitHub API error: HTTP {err.code}"]
            break
        except Exception as err:  # noqa: BLE001
            failures = [f"GitHub API error: {err}"]
            break

        missing_or_pending = []
        failing = []
        for check_name in sorted(required_checks):
            status, conclusion = check_state.get(check_name, ("missing", "missing"))
            if status != "completed":
                missing_or_pending.append((check_name, status, conclusion))
            elif conclusion not in SUCCESS_CONCLUSIONS:
                failing.append((check_name, status, conclusion))

        if not missing_or_pending and not failing:
            failures = []
            break

        failures = []
        for name, status, conclusion in missing_or_pending + failing:
            failures.append(f"{name}: status={status} conclusion={conclusion}")

        if attempt < args.retries:
            time.sleep(args.retry_delay_seconds)

    summary_lines = [
        f"Head SHA: `{args.head_sha}`",
        f"Required checks verified: `{', '.join(sorted(required_checks))}`",
    ]

    if failures:
        summary_lines.append("Result: `fail`")
        for failure in failures:
            summary_lines.append(f"Failure: {failure}")
        _write_summary(summary_lines)
        for failure in failures:
            print(failure)
        return 1

    summary_lines.append("Result: `pass`")
    _write_summary(summary_lines)
    print("All required checks are green on the current head SHA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
