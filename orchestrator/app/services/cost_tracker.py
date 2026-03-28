"""LLM cost tracking service.

Records per-call cost events to the existing FeatureEvent table and provides
aggregation helpers.  No new tables required.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeatureEvent, FeatureRequest
from app.services.event_logger import log_event

logger = logging.getLogger(__name__)


def record_cost(
    db: Session,
    feature: FeatureRequest,
    *,
    tier: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    operation: str,
) -> None:
    """Log a cost event to the feature's event log."""
    log_event(
        db,
        feature,
        event_type="llm_cost",
        actor_type="system",
        message=f"LLM call ({operation}): {model} [{tier}] ${cost_usd:.6f}",
        data={
            "tier": tier,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "operation": operation,
        },
    )
    logger.info(
        "llm_cost_recorded",
        extra={
            "feature_id": feature.id,
            "tier": tier,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd, 6),
            "operation": operation,
        },
    )


def get_feature_cost_summary(db: Session, feature_id: str) -> dict[str, Any]:
    """Query cost events for a feature and return aggregated summary."""
    rows = (
        db.execute(
            select(FeatureEvent)
            .where(FeatureEvent.feature_id == feature_id)
            .where(FeatureEvent.event_type == "llm_cost")
            .order_by(FeatureEvent.created_at)
        )
        .scalars()
        .all()
    )

    total_usd = 0.0
    by_tier: dict[str, float] = {}
    for row in rows:
        data = row.data or {}
        cost = float(data.get("cost_usd", 0))
        tier = str(data.get("tier", "unknown"))
        total_usd += cost
        by_tier[tier] = by_tier.get(tier, 0.0) + cost

    return {
        "total_usd": round(total_usd, 6),
        "calls": len(rows),
        "by_tier": {k: round(v, 6) for k, v in by_tier.items()},
    }
