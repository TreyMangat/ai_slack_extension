from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes.api import _assert_receipt_payload_match
from app.models import IntegrationCallbackReceipt


def _receipt(*, payload_hash: str) -> IntegrationCallbackReceipt:
    return IntegrationCallbackReceipt(
        idempotency_key="evt-1",
        feature_id="feature-1",
        event_type="preview_ready",
        payload_hash=payload_hash,
    )


def test_receipt_payload_match_allows_identical_hash() -> None:
    _assert_receipt_payload_match(existing=_receipt(payload_hash="abc123"), payload_hash="abc123")


def test_receipt_payload_match_rejects_mismatch() -> None:
    with pytest.raises(HTTPException) as exc:
        _assert_receipt_payload_match(existing=_receipt(payload_hash="abc123"), payload_hash="different")
    assert exc.value.status_code == 409
