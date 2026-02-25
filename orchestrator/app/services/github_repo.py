from __future__ import annotations

from typing import Any

from app.config import Settings


def parse_repo_slug(repo_value: str) -> tuple[str, str]:
    text = (repo_value or "").strip()
    if not text:
        return "", ""
    if text.startswith("<") and text.endswith(">"):
        # Slack links may arrive as <https://github.com/org/repo|label>.
        text = text[1:-1].strip()
    if "|" in text:
        text = text.split("|", 1)[0].strip()
    text = text.strip("`").strip()

    if text.startswith("https://github.com/"):
        tail = text[len("https://github.com/") :]
        tail = tail.removesuffix(".git")
        parts = [p for p in tail.split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]
        return "", ""

    if "/" in text:
        parts = [p for p in text.removesuffix(".git").split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]

    return "", ""


def resolve_repo_for_spec(*, spec: dict[str, Any], settings: Settings) -> tuple[str, str]:
    owner, repo = parse_repo_slug(str((spec or {}).get("repo") or "").strip())
    if owner and repo:
        return owner, repo

    fallback_owner = (settings.github_repo_owner or "").strip()
    fallback_repo = (settings.github_repo_name or "").strip()
    if fallback_owner and fallback_repo:
        return fallback_owner, fallback_repo
    return "", ""
