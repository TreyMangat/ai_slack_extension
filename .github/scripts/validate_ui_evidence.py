#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate UI evidence manifest")
    parser.add_argument("--policy", required=True, help="Path to risk policy YAML")
    parser.add_argument("--manifest", required=True, help="Path to UI evidence JSON manifest")
    parser.add_argument("--expected-head-sha", default="", help="Expected PR head SHA")
    return parser.parse_args()


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _load_yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("## UI Evidence Validation\n")
        for line in lines:
            f.write(f"- {line}\n")


def main() -> int:
    args = _parse_args()
    policy = _load_yaml(args.policy)
    manifest = _load_json(args.manifest)
    ui_cfg = policy.get("ui_evidence", {})

    required_flows = ui_cfg.get("required_flows", [])
    expected_entrypoint = ui_cfg.get("entrypoint", "")
    requires_identity_context = bool(ui_cfg.get("requires_identity_context", False))

    failures: list[str] = []
    status = manifest.get("status", "ok")
    if status != "ok":
        failures.append(f"Manifest status must be 'ok', got '{status}'.")

    for key in ["schema_version", "generated_at", "head_sha", "entrypoint", "flows"]:
        if key not in manifest:
            failures.append(f"Manifest missing required key: {key}.")

    if expected_entrypoint and manifest.get("entrypoint") != expected_entrypoint:
        failures.append(
            f"Manifest entrypoint mismatch. expected={expected_entrypoint} actual={manifest.get('entrypoint')}"
        )

    if args.expected_head_sha and manifest.get("head_sha") != args.expected_head_sha:
        failures.append(
            f"Manifest head SHA mismatch. expected={args.expected_head_sha} actual={manifest.get('head_sha')}"
        )

    flow_map = {flow.get("id"): flow for flow in manifest.get("flows", []) if isinstance(flow, dict)}
    for flow_id in required_flows:
        flow = flow_map.get(flow_id)
        if not flow:
            failures.append(f"Required UI flow missing: {flow_id}")
            continue
        if flow.get("status") != "passed":
            failures.append(f"Required UI flow did not pass: {flow_id}")

    if requires_identity_context:
        identity_context = manifest.get("identity_context", {})
        mode = identity_context.get("mode", "")
        if not mode:
            failures.append("Identity context is required but missing mode.")

    summary_lines = [
        f"Expected entrypoint: `{expected_entrypoint or 'not-set'}`",
        f"Required flows: `{', '.join(required_flows) or 'none'}`",
        f"Manifest path: `{args.manifest}`",
    ]

    if failures:
        summary_lines.append("Validation result: `fail`")
        for failure in failures:
            summary_lines.append(f"Failure: {failure}")
        _write_summary(summary_lines)
        for failure in failures:
            print(failure)
        return 1

    summary_lines.append("Validation result: `pass`")
    _write_summary(summary_lines)
    print("UI evidence manifest validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
