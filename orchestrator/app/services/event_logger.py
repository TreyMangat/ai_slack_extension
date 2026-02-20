from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import FeatureEvent, FeatureRequest


def log_event(
    db: Session,
    feature: FeatureRequest,
    *,
    event_type: str,
    message: str = "",
    actor_type: str = "system",
    actor_id: str = "",
    data: dict[str, Any] | None = None,
) -> FeatureEvent:
    ev = FeatureEvent(
        feature_id=feature.id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        message=message,
        data=data or {},
    )
    db.add(ev)
    return ev
