from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from rich.console import Console
from sqlalchemy import func, select

from app.config import get_settings
from app.db import db_session
from app.models import FeatureEvent, FeatureRequest
from app.services.event_logger import log_event
from app.services.slack_adapter import get_slack_adapter
from app.services.workspace_service import cleanup_old_workspaces
from app.state_machine import PR_OPENED


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


def run_stale_callback_alerts_once() -> None:
    settings = get_settings()
    stale_minutes = max(settings.callback_stale_alert_minutes, 1)
    cooldown_minutes = max(settings.callback_stale_alert_cooldown_minutes, 1)
    max_rows = max(settings.callback_stale_check_max_per_run, 1)
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=stale_minutes)
    cooldown_cutoff = now - timedelta(minutes=cooldown_minutes)
    slack = get_slack_adapter()
    alerted = 0

    with db_session() as db:
        candidates = (
            db.execute(
                select(FeatureRequest)
                .where(FeatureRequest.status == PR_OPENED)
                .where(FeatureRequest.updated_at <= stale_cutoff)
                .order_by(FeatureRequest.updated_at.asc())
                .limit(max_rows)
            )
            .scalars()
            .all()
        )

        for feature in candidates:
            recent_count = (
                db.execute(
                    select(func.count())
                    .select_from(FeatureEvent)
                    .where(FeatureEvent.feature_id == feature.id)
                    .where(FeatureEvent.event_type == "callback_stale_alerted")
                    .where(FeatureEvent.created_at >= cooldown_cutoff)
                )
                .scalar_one()
            )
            if int(recent_count or 0) > 0:
                continue

            message = (
                f"Feature has been in PR_OPENED without preview callback for at least {stale_minutes} minutes."
            )
            log_event(
                db,
                feature,
                event_type="callback_stale_alerted",
                actor_type="system",
                actor_id="cleanup-worker",
                message=message,
                data={
                    "status": feature.status,
                    "updated_at": feature.updated_at.isoformat() if feature.updated_at else "",
                    "stale_minutes": stale_minutes,
                },
            )
            alerted += 1
            if feature.slack_channel_id and feature.slack_thread_ts:
                try:
                    slack.post_thread_message(
                        channel=feature.slack_channel_id,
                        thread_ts=feature.slack_thread_ts,
                        text=(
                            f"Build status is still `{feature.status}` for *{feature.title}*.\n"
                            f"No preview callback received in {stale_minutes}+ minutes."
                        ),
                    )
                except Exception as e:  # noqa: BLE001
                    console.print(f"[yellow]failed to post stale callback alert to Slack: {e}[/yellow]")

    if alerted:
        console.print(f"[yellow]stale callback alerts emitted: {alerted}[/yellow]")


def main() -> None:
    settings = get_settings()
    interval_minutes = max(settings.workspace_cleanup_interval_minutes, 1)
    interval_seconds = interval_minutes * 60
    console.print(f"[green]Starting workspace cleanup worker (interval={interval_minutes}m)[/green]")

    while True:
        try:
            run_cleanup_once()
            run_stale_callback_alerts_once()
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]workspace cleanup worker error: {e}[/yellow]")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
