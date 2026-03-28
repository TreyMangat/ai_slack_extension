from __future__ import annotations

from types import SimpleNamespace

import app.api.routes.ui as ui_mod


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeDb:
    def __init__(self, feature, llm_cost_events):
        self._feature = feature
        self._llm_cost_events = llm_cost_events

    def get(self, _model, _feature_id):
        return self._feature

    def execute(self, _stmt):
        return _FakeExecuteResult(self._llm_cost_events)


def _feature():
    return SimpleNamespace(
        id="feature-123",
        requester_user_id="user-1",
        status="READY_FOR_BUILD",
        events=[
            SimpleNamespace(created_at=2, event_type="spec_validated", message="Spec valid"),
            SimpleNamespace(created_at=1, event_type="created", message="Created"),
        ],
    )


def test_feature_detail_passes_llm_costs_when_events_exist(monkeypatch) -> None:
    feature = _feature()
    llm_cost_events = [
        SimpleNamespace(
            data={"cost_usd": 0.0123, "tier": "mini", "model": "qwen/qwen3.5-9b"},
            created_at=3,
        ),
        SimpleNamespace(
            data={"cost_usd": 0.0456, "tier": "frontier", "model": "anthropic/claude-opus-4-6"},
            created_at=4,
        ),
    ]
    db = _FakeDb(feature, llm_cost_events)
    captured: dict[str, object] = {}

    monkeypatch.setattr(ui_mod, "user_can_access_feature", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        ui_mod.templates,
        "TemplateResponse",
        lambda name, context: captured.update({"name": name, "context": context}) or context,
    )

    response = ui_mod.feature_detail(
        feature_id="feature-123",
        request=SimpleNamespace(),
        db=db,
        user=SimpleNamespace(),
    )

    assert response["llm_costs"] == {
        "total_usd": 0.0579,
        "calls": 2,
        "by_tier": {"mini": 0.0123, "frontier": 0.0456},
        "by_tier_calls": {"mini": 1, "frontier": 1},
        "models": ["qwen/qwen3.5-9b", "anthropic/claude-opus-4-6"],
    }
    assert captured["name"] == "feature_detail.html"


def test_feature_detail_passes_none_when_no_cost_events(monkeypatch) -> None:
    feature = _feature()
    db = _FakeDb(feature, [])
    captured: dict[str, object] = {}

    monkeypatch.setattr(ui_mod, "user_can_access_feature", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        ui_mod.templates,
        "TemplateResponse",
        lambda name, context: captured.update({"name": name, "context": context}) or context,
    )

    response = ui_mod.feature_detail(
        feature_id="feature-123",
        request=SimpleNamespace(),
        db=db,
        user=SimpleNamespace(),
    )

    assert response["llm_costs"] is None
    assert captured["name"] == "feature_detail.html"
