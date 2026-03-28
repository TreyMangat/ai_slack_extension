from __future__ import annotations

from typing import Any


def _coerce_event_data(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        data = event.get("data")
        if isinstance(data, dict):
            return data
        return {}
    data = getattr(event, "data", {})
    return data if isinstance(data, dict) else {}


def _is_llm_cost_event(event: Any) -> bool:
    if isinstance(event, dict):
        event_type = str(event.get("event_type") or "").strip()
        return (not event_type) or event_type == "llm_cost"
    event_type = str(getattr(event, "event_type", "") or "").strip()
    return (not event_type) or event_type == "llm_cost"


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_llm_costs(events: list[Any]) -> dict[str, Any] | None:
    total_usd = 0.0
    calls = 0
    by_tier = {"mini": 0.0, "frontier": 0.0}
    by_tier_calls = {"mini": 0, "frontier": 0}
    models: list[str] = []

    for event in events:
        if not _is_llm_cost_event(event):
            continue
        data = _coerce_event_data(event)
        cost = _coerce_float(
            data.get("cost_estimate_usd")
            or data.get("cost_estimate")
            or data.get("cost_usd")
        )
        tier = str(data.get("tier") or data.get("model_tier") or "").strip().lower()
        model = str(data.get("model") or "").strip()

        total_usd += cost
        calls += 1

        if tier in by_tier:
            by_tier[tier] += cost
            by_tier_calls[tier] += 1

        if model and model not in models:
            models.append(model)

    if calls <= 0:
        return None

    return {
        "total_usd": round(total_usd, 6),
        "calls": calls,
        "by_tier": {
            "mini": round(by_tier["mini"], 6),
            "frontier": round(by_tier["frontier"], 6),
        },
        "by_tier_calls": by_tier_calls,
        "models": models,
    }


def build_llm_cost_context_block(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not summary:
        return None

    calls = int(summary.get("calls") or 0)
    by_tier_calls = summary.get("by_tier_calls") or {}
    mini_calls = int(by_tier_calls.get("mini") or 0)
    frontier_calls = int(by_tier_calls.get("frontier") or 0)
    total_usd = _coerce_float(summary.get("total_usd"))

    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f":bar_chart: _LLM cost: ${total_usd:.4f} "
                    f"({calls} calls - {mini_calls} mini, {frontier_calls} frontier)_"
                ),
            }
        ],
    }
