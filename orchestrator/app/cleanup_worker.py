from __future__ import annotations

import time

from rich.console import Console

from app.config import get_settings
from app.db import db_session
from app.models import FeatureRequest
from app.services.workspace_service import cleanup_old_workspaces


console = Console()


def _retention_hours_for_feature(feature: FeatureRequest | None) -> int:
    settings = get_settings()
    if feature is None:
        return max(settings.workspace_retention_hours_without_pr, 1)

    status = str(feature.status or "").strip().upper()
    has_pr = bool(str(feature.github_pr_url or "").strip())
    if has_pr:
        return max(settings.workspace_retention_hours_with_pr, 1)
    if status in {"FAILED_SPEC", "FAILED_BUILD", "FAILED_PREVIEW", "NEEDS_INFO", "NEEDS_HUMAN"}:
        return max(settings.workspace_retention_hours_failed, 1)
    return max(settings.workspace_retention_hours_without_pr, 1)


def run_cleanup_once() -> None:
    with db_session() as db:
        def resolver(feature_id: str) -> int:
            row = db.get(FeatureRequest, feature_id)
            return _retention_hours_for_feature(row)

        result = cleanup_old_workspaces(retention_resolver=resolver)

    removed = len(result.removed_paths)
    errors = len(result.errors)
    if removed or errors:
        console.print(
            f"[cyan]workspace cleanup run finished: removed={removed} errors={errors} root={result.workspace_root}[/cyan]"
        )


def main() -> None:
    settings = get_settings()
    interval_minutes = max(settings.workspace_cleanup_interval_minutes, 1)
    interval_seconds = interval_minutes * 60
    console.print(f"[green]Starting workspace cleanup worker (interval={interval_minutes}m)[/green]")

    while True:
        try:
            run_cleanup_once()
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]workspace cleanup worker error: {e}[/yellow]")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
