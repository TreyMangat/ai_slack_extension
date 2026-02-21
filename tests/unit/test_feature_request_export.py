from __future__ import annotations

from datetime import datetime, timezone

from app.api.routes import api
from app.models import FeatureRequest
from app.security import AuthenticatedUser


def _feature(*, feature_id: str, title: str, status: str, requester: str, spec: dict) -> FeatureRequest:
    return FeatureRequest(
        id=feature_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        title=title,
        status=status,
        requester_user_id=requester,
        spec=spec,
    )


def test_serialize_feature_requests_csv_includes_expected_columns() -> None:
    csv_data = api._serialize_feature_requests_csv(
        [
            _feature(
                feature_id="feat-1",
                title="Smoke test export invoices",
                status="READY_FOR_BUILD",
                requester="finance",
                spec={"repo": "acme/billing", "implementation_mode": "new_feature"},
            )
        ]
    )

    lines = csv_data.splitlines()
    assert lines[0] == "id,created_at,updated_at,status,title,requester_user_id,repo,implementation_mode"
    assert "Smoke test export invoices" in lines[1]
    assert "acme/billing" in lines[1]


def test_build_feature_filters_respects_status_and_mine_flag(monkeypatch) -> None:
    user = AuthenticatedUser(
        user_id="analyst",
        email="analyst@example.com",
        groups=set(),
        auth_source="disabled",
    )

    monkeypatch.setattr(api, "user_can_view_all_features", lambda _user: True)
    conditions, should_return_empty = api._build_feature_filters(user=user, status="READY_FOR_BUILD", mine=False)

    assert should_return_empty is False
    assert len(conditions) == 1

    monkeypatch.setattr(api, "user_can_view_all_features", lambda _user: False)
    conditions, should_return_empty = api._build_feature_filters(user=user, status="", mine=False)

    assert should_return_empty is False
    # requester filter should be added when user cannot view all
    assert len(conditions) == 1
