from __future__ import annotations

from app.config import get_settings
from app.models import FeatureRequest
from app.services.slack_adapter import SlackAdapter


def is_approver_allowed(approver: str) -> bool:
    settings = get_settings()
    allowlist = settings.reviewer_allowed_user_set()
    if not allowlist:
        return True
    return approver in allowlist


def ensure_approver_allowed(approver: str) -> None:
    if not is_approver_allowed(approver):
        raise PermissionError(f"Approver '{approver}' is not in REVIEWER_ALLOWED_USERS")


def _reviewer_mentions() -> str:
    settings = get_settings()
    users = sorted(settings.reviewer_allowed_user_set())
    if not users:
        return ""
    return " ".join(f"<@{u}>" for u in users)


def notify_reviewer_for_approval(feature: FeatureRequest, slack: SlackAdapter) -> bool:
    """Notify reviewer/admin that a feature is ready for approval.

    Returns True if a notification was attempted, else False.
    """

    settings = get_settings()
    mentions = _reviewer_mentions()

    dashboard_url = f"{settings.base_url}/features/{feature.id}"
    text = (
        f"{mentions + ' ' if mentions else ''}"
        f"Review requested for *{feature.title}* (`{feature.id}`).\n"
        f"PR: {feature.github_pr_url or '(pending)'}\n"
        f"Preview: {feature.preview_url or '(pending)'}\n"
        f"Dashboard: {dashboard_url}"
    )

    # If a reviewer channel is configured, notify there.
    if settings.reviewer_channel_id:
        slack.post_channel_message(channel=settings.reviewer_channel_id, text=text, team_id=feature.slack_team_id)
        return True

    # Otherwise, notify in the feature thread if available.
    if feature.slack_channel_id and feature.slack_thread_ts:
        slack.post_thread_message(
            channel=feature.slack_channel_id,
            thread_ts=feature.slack_thread_ts,
            text=text,
            team_id=feature.slack_team_id,
        )
        return True

    return False
