from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.prompt_optimizer import detect_ui_feature


def _as_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _lines(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _truncate(value: Any, *, max_chars: int = 1600) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _summary_bullets(summary: str, *, ui_feature: bool) -> list[str]:
    raw_lines: list[str] = []
    for chunk in str(summary or "").replace("\r", "\n").split("\n"):
        cleaned = chunk.strip().lstrip("-").strip()
        if cleaned:
            raw_lines.append(cleaned)

    bullets: list[str] = []
    for item in raw_lines:
        if item in bullets:
            continue
        bullets.append(item)
        if len(bullets) >= 4:
            break

    if len(bullets) < 2:
        bullets.append("Implemented the request using the structured Slack spec and acceptance criteria.")
    if len(bullets) < 2:
        bullets.append("Updated tests/verification steps to keep the change reviewable.")
    if ui_feature and len(bullets) < 3:
        bullets.append("Ensured UI output is demonstrable through PR preview/deployment checks.")

    return bullets[:4]


def _frontend_root(repo_path: Path | None) -> str:
    if repo_path is None:
        return "web"
    for candidate in ["web", "ui"]:
        if (repo_path / candidate / "package.json").exists():
            return candidate
    if (repo_path / "package.json").exists():
        return "."
    return "web"


def _test_commands(
    *,
    spec: dict[str, Any],
    default_test_command: str,
    repo_path: Path | None,
) -> list[str]:
    ui_feature = bool(spec.get("ui_feature"))
    commands: list[str] = []
    if default_test_command.strip():
        commands.append(default_test_command.strip())

    if ui_feature:
        web_root = _frontend_root(repo_path)
        if web_root == ".":
            commands.extend(["npm ci", "npm run build"])
        else:
            commands.extend([f"cd {web_root}", "npm ci", "npm run build"])
    deduped: list[str] = []
    for cmd in commands:
        cleaned = cmd.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def build_standard_pr_body(
    *,
    spec: dict[str, Any],
    feature_id: str,
    issue_number: int | None,
    branch_name: str,
    runner_name: str,
    runner_model: str,
    summary: str,
    verification_output: str,
    verification_command: str,
    verification_warning: str,
    preview_url: str,
    cloudflare_project_name: str,
    cloudflare_production_branch: str,
    repo_path: Path | None = None,
) -> str:
    final_spec = dict(spec or {})
    ui_feature, ui_keywords = detect_ui_feature(final_spec)
    if bool(final_spec.get("ui_feature")):
        ui_feature = True
    final_spec["ui_feature"] = ui_feature

    title = _as_text(final_spec.get("title"), "Feature request")
    problem = _as_text(final_spec.get("problem"), "No problem statement provided.")
    why_now = _as_text(final_spec.get("business_justification"), "No urgency context provided.")

    criteria = _lines(final_spec.get("acceptance_criteria"))
    criteria_lines = "\n".join([f"- [ ] {item}" for item in criteria]) if criteria else "- [ ] Confirm behavior matches request intent."

    bullets = _summary_bullets(summary, ui_feature=ui_feature)
    change_lines = "\n".join([f"- {item}" for item in bullets])

    local_test_commands = _test_commands(
        spec=final_spec,
        default_test_command=verification_command,
        repo_path=repo_path,
    )
    test_block = "\n".join(local_test_commands) if local_test_commands else "echo \"No local test command configured\""

    preview_instructions = ""
    if ui_feature:
        project_label = cloudflare_project_name.strip() or "(set CLOUDFLARE_PAGES_PROJECT_NAME)"
        prod_branch = cloudflare_production_branch.strip() or "main"
        if preview_url.strip():
            preview_instructions = (
                "## UI Preview\n"
                f"- Preview URL: {preview_url.strip()}\n"
                "- If this URL is stale, open the PR Checks tab and click the latest Cloudflare Pages deployment.\n"
            )
        else:
            preview_instructions = (
                "## UI Preview\n"
                "- Open this PR and go to the Checks tab.\n"
                "- Click the Cloudflare Pages deployment check and then View deployment.\n"
                f"- Expected Cloudflare Pages project: `{project_label}` (production branch: `{prod_branch}`).\n"
                "- Add screenshot/GIF evidence in a PR comment when possible.\n"
            )
    else:
        example_snippet = _truncate(verification_output or summary or "(no output captured)")
        preview_instructions = (
            "## Example Output / Logs\n"
            "```text\n"
            f"{example_snippet}\n"
            "```\n"
        )

    warning_block = ""
    if verification_warning.strip():
        warning_block = (
            "\n## Verification Warning\n"
            f"- {verification_warning.strip()}\n"
        )

    issue_line = f"#{issue_number}" if issue_number else "(not linked)"
    ui_keywords_line = ", ".join(ui_keywords) if ui_keywords else "(none)"

    return (
        "## Why\n"
        f"- User request: {title}\n"
        f"- Problem summary: {problem}\n"
        f"- Why now: {why_now}\n\n"
        "## What Changed\n"
        f"{change_lines}\n\n"
        "## Acceptance Criteria\n"
        f"{criteria_lines}\n\n"
        "## How To Test Locally\n"
        "```bash\n"
        f"{test_block}\n"
        "```\n\n"
        f"{preview_instructions}\n"
        "## Metadata\n"
        f"- Feature request id: {feature_id or '(unknown)'}\n"
        f"- Linked issue: {issue_line}\n"
        f"- Branch: `{branch_name or '(unknown)'}`\n"
        f"- Runner: `{runner_name}`\n"
        f"- Model: `{runner_model or '(unspecified)'}`\n"
        f"- UI feature: `{str(ui_feature).lower()}`\n"
        f"- Matched UI keywords: {ui_keywords_line}\n"
        f"{warning_block}"
    ).strip()

