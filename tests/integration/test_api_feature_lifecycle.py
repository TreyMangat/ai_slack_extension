"""Integration tests: REST API endpoints end-to-end with TestClient.

Tests feature creation, revalidation, build enqueue, approval auth,
health/runtime, and feature detail including cost data.

Builds a minimal FastAPI app directly from routers (avoiding app.main
module-level side effects like static file mounts and Slack bolt init).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.models import Base, FeatureEvent, FeatureRequest
from app.services.openrouter_provider import ModelTier, OpenRouterResponse
from app.state_machine import NEW, PREVIEW_READY, READY_FOR_BUILD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _or_response(content_dict, *, model="anthropic/claude-opus-4-6", tier=ModelTier.FRONTIER):
    return OpenRouterResponse(
        content=json.dumps(content_dict) if isinstance(content_dict, dict) else content_dict,
        model=model,
        usage={"input_tokens": 50, "output_tokens": 100},
        cost_estimate=0.001,
        tier=tier,
    )


def _valid_spec():
    return {
        "title": "Add dark mode",
        "problem": "Users want dark mode",
        "business_justification": "High user demand",
        "acceptance_criteria": ["Dark mode toggle works"],
        "repo": "org/app",
        "implementation_mode": "new_feature",
    }


def _valid_payload():
    return {
        "spec": _valid_spec(),
        "requester_user_id": "local-user",
    }


# ---------------------------------------------------------------------------
# App fixture — monkeypatches app.db to use in-memory SQLite
# ---------------------------------------------------------------------------

@pytest.fixture
def test_app(mock_settings, monkeypatch):
    """Lightweight FastAPI test app backed by in-memory SQLite."""
    import app.db as db_mod

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Monkeypatch the db module's SessionLocal so get_db uses our engine
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession)

    from app.api.routes import api, health

    app = FastAPI()
    app.include_router(health.router, tags=["health"])
    app.include_router(api.router, prefix="/api", tags=["api"])

    client = TestClient(app, raise_server_exceptions=True)
    return client, TestSession


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateFeatureReturns201:
    def test_create_feature_returns_201(self, test_app):
        """POST /api/feature-requests creates a feature."""
        client, _ = test_app

        llm_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.95,
        }
        mock_resp = _or_response(llm_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            resp = client.post("/api/feature-requests", json=_valid_payload())

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] in {NEW, READY_FOR_BUILD}
        assert data["title"] == "Add dark mode"


class TestRevalidateCallsFrontierModel:
    def test_revalidate_calls_frontier_model(self, test_app):
        """POST /api/feature-requests/{id}/revalidate uses frontier LLM."""
        client, Session = test_app

        llm_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.95,
        }
        mock_resp = _or_response(llm_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            create_resp = client.post("/api/feature-requests", json=_valid_payload())
        feature_id = create_resp.json()["id"]

        revalidate_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": ["Looks good"],
            "confidence": 0.98,
        }
        mock_resp2 = _or_response(revalidate_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp2,
        ) as mock_call:
            resp = client.post(f"/api/feature-requests/{feature_id}/revalidate")

        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_spec_analysis"] is not None
        assert data["llm_spec_analysis"]["confidence"] == 0.98

        mock_call.assert_called()
        call_kwargs = mock_call.call_args
        assert call_kwargs[1]["tier"] == ModelTier.FRONTIER


class TestBuildEndpointEnqueuesJob:
    def test_build_endpoint_enqueues_job(self, test_app):
        """POST /api/feature-requests/{id}/build enqueues a job via RQ."""
        client, Session = test_app

        llm_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.95,
        }
        mock_resp = _or_response(llm_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            create_resp = client.post("/api/feature-requests", json=_valid_payload())
        feature_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == READY_FOR_BUILD

        mock_job = MagicMock()
        mock_job.id = "job-123"
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_job

        with patch("app.api.routes.api.get_queue", return_value=mock_queue):
            resp = client.post(f"/api/feature-requests/{feature_id}/build")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["enqueued"] is True
        assert data["job_id"] == "job-123"
        mock_queue.enqueue.assert_called_once()


class TestApproveRequiresApproverRole:
    def test_approve_requires_approver_role(self, test_app, monkeypatch):
        """Approval with api_token auth requires appropriate token."""
        client, Session = test_app

        llm_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.95,
        }
        mock_resp = _or_response(llm_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            create_resp = client.post("/api/feature-requests", json=_valid_payload())
        feature_id = create_resp.json()["id"]

        # Force feature to PREVIEW_READY for approval
        db = Session()
        feature = db.get(FeatureRequest, feature_id)
        feature.status = PREVIEW_READY
        db.commit()
        db.close()

        # With auth disabled (default), approve succeeds
        resp = client.post(f"/api/feature-requests/{feature_id}/approve")
        assert resp.status_code == 200

        # Enable api_token auth - without token should fail
        monkeypatch.setenv("AUTH_MODE", "api_token")
        monkeypatch.setenv("API_AUTH_TOKEN", "secret-token-123")
        get_settings.cache_clear()

        resp2 = client.post(f"/api/feature-requests/{feature_id}/approve")
        assert resp2.status_code == 401

        # With correct token, auth passes (state error expected, not auth error)
        resp3 = client.post(
            f"/api/feature-requests/{feature_id}/approve",
            headers={"X-FF-Token": "secret-token-123"},
        )
        assert resp3.status_code != 401
        assert resp3.status_code != 403


class TestHealthRuntimeShowsOpenRouter:
    def test_health_runtime_shows_openrouter(self, test_app):
        """GET /health/runtime includes OpenRouter configuration."""
        client, _ = test_app
        resp = client.get("/health/runtime")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "openrouter" in data
        assert data["openrouter"]["configured"] is True
        assert data["openrouter"]["mini_model"] == "qwen/qwen3.5-9b"
        assert data["openrouter"]["frontier_model"] == "anthropic/claude-opus-4-6"


class TestFeatureDetailIncludesCostSummary:
    def test_feature_detail_includes_cost_summary(self, test_app):
        """GET /api/feature-requests/{id} includes llm_spec_analysis with cost data."""
        client, _ = test_app

        llm_analysis = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.92,
        }
        mock_resp = _or_response(llm_analysis)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            create_resp = client.post("/api/feature-requests", json=_valid_payload())
        feature_id = create_resp.json()["id"]

        resp = client.get(f"/api/feature-requests/{feature_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_spec_analysis"] is not None
        assert data["llm_spec_analysis"]["model"] == "anthropic/claude-opus-4-6"
        assert data["llm_spec_analysis"]["tier"] == "frontier"
        assert "cost_estimate_usd" in data["llm_spec_analysis"]

        cost_events = [e for e in data["events"] if e["event_type"] == "llm_cost"]
        assert len(cost_events) >= 1
