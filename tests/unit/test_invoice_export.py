from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.api.routes.api import _feature_query_conditions, _render_feature_export_csv
from app.security import AuthenticatedUser


def _user(*, user_id: str = "alice", groups: set[str] | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        email=f"{user_id}@example.com",
        groups=groups or {"engineering"},
        auth_source="disabled",
    )


def test_export_csv_includes_header_and_rows() -> None:
    rows = [
        SimpleNamespace(
            id="inv-1",
            title="Invoice A",
            status="open",
            requester_user_id="alice",
            created_at=datetime(2025, 1, 1, 12, 0, 0),
        ),
        SimpleNamespace(
            id="inv-2",
            title="Invoice B",
            status="paid",
            requester_user_id="bob",
            created_at=datetime(2025, 1, 2, 12, 0, 0),
        ),
    ]

    rendered = _render_feature_export_csv(rows)

    assert "feature_id,title,status,requester_user_id,created_at" in rendered
    assert "inv-1,Invoice A,open,alice,2025-01-01T12:00:00" in rendered
    assert "inv-2,Invoice B,paid,bob,2025-01-02T12:00:00" in rendered


def test_export_filter_conditions_include_status_and_identity_for_mine() -> None:
    conditions, allowed = _feature_query_conditions(user=_user(), status="open", mine=True)

    assert allowed is True
    assert len(conditions) == 2


def test_export_filter_conditions_skip_identity_for_admin_all_view() -> None:
    conditions, allowed = _feature_query_conditions(user=_user(groups={"admins"}), status="", mine=False)

    assert allowed is True
    assert conditions == []
