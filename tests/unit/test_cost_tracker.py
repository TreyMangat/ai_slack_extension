from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.cost_tracker import get_feature_cost_summary, record_cost


class TestRecordCost:
    def test_creates_feature_event(self) -> None:
        mock_db = MagicMock()
        mock_feature = MagicMock()
        mock_feature.id = "feat-123"

        with patch("app.services.cost_tracker.log_event") as mock_log:
            record_cost(
                mock_db,
                mock_feature,
                tier="frontier",
                model="anthropic/claude-opus-4-6",
                tokens_in=200,
                tokens_out=100,
                cost_usd=0.0105,
                operation="spec_validation",
            )

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs[1]["event_type"] == "llm_cost"
        assert call_kwargs[1]["data"]["tier"] == "frontier"
        assert call_kwargs[1]["data"]["model"] == "anthropic/claude-opus-4-6"
        assert call_kwargs[1]["data"]["tokens_in"] == 200
        assert call_kwargs[1]["data"]["tokens_out"] == 100
        assert call_kwargs[1]["data"]["cost_usd"] == 0.0105
        assert call_kwargs[1]["data"]["operation"] == "spec_validation"

    def test_message_contains_model_and_cost(self) -> None:
        mock_db = MagicMock()
        mock_feature = MagicMock()
        mock_feature.id = "feat-456"

        with patch("app.services.cost_tracker.log_event") as mock_log:
            record_cost(
                mock_db,
                mock_feature,
                tier="mini",
                model="qwen/qwen3.5-9b",
                tokens_in=50,
                tokens_out=20,
                cost_usd=0.0001,
                operation="intake_classify",
            )

        msg = mock_log.call_args[1]["message"]
        assert "qwen/qwen3.5-9b" in msg
        assert "mini" in msg
        assert "intake_classify" in msg


class TestGetFeatureCostSummary:
    def test_empty_events(self) -> None:
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        result = get_feature_cost_summary(mock_db, "feat-999")
        assert result == {"total_usd": 0.0, "calls": 0, "by_tier": {}}

    def test_aggregation(self) -> None:
        mock_event_1 = MagicMock()
        mock_event_1.data = {"cost_usd": 0.01, "tier": "frontier"}
        mock_event_2 = MagicMock()
        mock_event_2.data = {"cost_usd": 0.0001, "tier": "mini"}
        mock_event_3 = MagicMock()
        mock_event_3.data = {"cost_usd": 0.02, "tier": "frontier"}

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = [
            mock_event_1, mock_event_2, mock_event_3,
        ]

        result = get_feature_cost_summary(mock_db, "feat-100")
        assert result["calls"] == 3
        assert result["total_usd"] == pytest.approx(0.0301, abs=1e-6)
        assert result["by_tier"]["frontier"] == pytest.approx(0.03, abs=1e-6)
        assert result["by_tier"]["mini"] == pytest.approx(0.0001, abs=1e-6)

    def test_missing_data_fields(self) -> None:
        mock_event = MagicMock()
        mock_event.data = {}  # no cost_usd or tier

        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = [mock_event]

        result = get_feature_cost_summary(mock_db, "feat-200")
        assert result["calls"] == 1
        assert result["total_usd"] == 0.0
        assert result["by_tier"]["unknown"] == 0.0
