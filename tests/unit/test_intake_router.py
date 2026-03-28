from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.services.intake_router import (
    IntakeAction,
    classify_intake_message,
    escalate_to_frontier,
    _gather_intake_context,
    _get_user_history,
)
from app.services.github_connection import GitHubConnectionCheck, GitHubConnectionStatus
from app.services.openrouter_provider import ModelTier, OpenRouterError, OpenRouterResponse


def _connected_github_status() -> GitHubConnectionCheck:
    """Helper: a GitHubConnectionCheck that says CONNECTED."""
    return GitHubConnectionCheck(
        status=GitHubConnectionStatus.CONNECTED,
        username="testuser",
        repos_available=True,
        message="Connected as @testuser",
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    defaults = {
        "DATABASE_URL": "postgresql+psycopg2://x:x@db:5432/x",
        "REDIS_URL": "redis://redis:6379/0",
        "SECRET_KEY": "test",
        "OPENROUTER_API_KEY": "sk-or-test-key",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def _mock_openrouter_response(content_dict: dict) -> OpenRouterResponse:
    return OpenRouterResponse(
        content=json.dumps(content_dict),
        model="qwen/qwen3.5-9b",
        usage={"input_tokens": 30, "output_tokens": 20},
        cost_estimate=0.0001,
        tier=ModelTier.MINI,
    )


# ---------------------------------------------------------------------------
# Cancel keyword short-circuit
# ---------------------------------------------------------------------------


class TestCancelShortCircuit:
    @pytest.mark.asyncio
    async def test_cancel_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock) as mock_call:
            result = await classify_intake_message("cancel", [], {})
        assert result.action == "cancel"
        assert result.confidence == 1.0
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock) as mock_call:
            result = await classify_intake_message("stop", [], {})
        assert result.action == "cancel"
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_quit_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock) as mock_call:
            result = await classify_intake_message("quit", [], {})
        assert result.action == "cancel"
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_nevermind_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock) as mock_call:
            result = await classify_intake_message("nevermind", [], {})
        assert result.action == "cancel"
        mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


class TestFieldExtraction:
    @pytest.mark.asyncio
    async def test_extracts_title_and_description(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "title",
            "field_value": "Add dark mode to dashboard",
            "next_question": "Which repo should this go into?",
            "confidence": 0.9,
            "reasoning": "User clearly stated a feature request title",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message(
                "I want to add dark mode to the dashboard",
                [],
                {},
            )
        assert result.action == "ask_field"
        assert result.field_name == "title"
        assert result.field_value == "Add dark mode to dashboard"
        assert result.next_question == "Which repo should this go into?"

    @pytest.mark.asyncio
    async def test_with_existing_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "branch",
            "field_value": "feat/dark-mode",
            "next_question": "What are the acceptance criteria?",
            "confidence": 0.85,
            "reasoning": "Extracted branch from message",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message(
                "use the feat/dark-mode branch",
                [{"role": "user", "text": "Add dark mode"}],
                {"title": "Dark mode", "repo": "org/frontend"},
            )
        assert result.action == "ask_field"
        assert result.field_name == "branch"
        assert result.field_value == "feat/dark-mode"

    @pytest.mark.asyncio
    async def test_confirm_when_all_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "confirm",
            "confidence": 0.95,
            "reasoning": "All required fields collected",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message(
                "yes that looks good",
                [],
                {"title": "X", "description": "Y", "repo": "a/b", "branch": "main", "acceptance_criteria": "works"},
            )
        assert result.action == "confirm"


# ---------------------------------------------------------------------------
# Confidence thresholding
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_clarify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "title",
            "field_value": "maybe something",
            "confidence": 0.3,
            "reasoning": "Very ambiguous message",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("hmm idk", [], {})
        assert result.action == "clarify"

    @pytest.mark.asyncio
    async def test_high_confidence_keeps_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "title",
            "field_value": "Add search",
            "confidence": 0.9,
            "reasoning": "Clear request",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("I need a search feature", [], {})
        assert result.action == "ask_field"


# ---------------------------------------------------------------------------
# Fallback on OpenRouterError
# ---------------------------------------------------------------------------


class TestFallbackOnError:
    @pytest.mark.asyncio
    async def test_openrouter_error_returns_clarify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "internal error"),
        ):
            result = await classify_intake_message("add a feature", [], {})
        assert result.action == "clarify"
        assert result.next_question == "Sorry, could you rephrase that?"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_clarify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        bad_response = OpenRouterResponse(
            content="not valid json at all",
            model="qwen/qwen3.5-9b",
            usage={"input_tokens": 10, "output_tokens": 5},
            cost_estimate=0.0001,
            tier=ModelTier.MINI,
        )
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=bad_response):
            result = await classify_intake_message("add a feature", [], {})
        assert result.action == "clarify"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# New IntakeAction fields (user_skill, suggested_repo, suggested_branch)
# ---------------------------------------------------------------------------


class TestNewIntakeActionFields:
    def test_defaults_are_backward_compatible(self) -> None:
        """Creating IntakeAction without new fields doesn't break."""
        action = IntakeAction(action="ask_field", confidence=0.9, reasoning="test")
        assert action.user_skill == "technical"
        assert action.suggested_repo is None
        assert action.suggested_branch is None

    @pytest.mark.asyncio
    async def test_developer_message_detected_as_developer_skill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "description",
            "field_value": None,
            "next_question": "What origins should be allowed?",
            "confidence": 0.9,
            "reasoning": "Technical user, gave repo and branch",
            "user_skill": "developer",
            "suggested_repo": "org/infra-services",
            "suggested_branch": "feature/cors",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message(
                "Add CORS headers to infra-services on feature/cors",
                [], {},
            )
        assert result.user_skill == "developer"
        assert result.suggested_repo == "org/infra-services"
        assert result.suggested_branch == "feature/cors"

    @pytest.mark.asyncio
    async def test_vague_message_detected_as_non_technical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "clarify",
            "field_name": None,
            "field_value": None,
            "next_question": "Which part of the app?",
            "confidence": 0.7,
            "reasoning": "Vague request",
            "user_skill": "non_technical",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("The app should look better on mobile", [], {})
        assert result.user_skill == "non_technical"

    @pytest.mark.asyncio
    async def test_repo_suggestion_populated_from_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "repo",
            "field_value": "org/frontend",
            "next_question": "Which branch?",
            "confidence": 0.85,
            "reasoning": "Matched from catalog",
            "suggested_repo": "org/frontend",
            "suggested_branch": "main",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("Put it in the frontend repo", [], {})
        assert result.suggested_repo == "org/frontend"
        assert result.suggested_branch == "main"


# ---------------------------------------------------------------------------
# Escalation logic
# ---------------------------------------------------------------------------


class TestEscalation:
    @pytest.mark.asyncio
    async def test_escalation_calls_frontier_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        frontier_response = OpenRouterResponse(
            content=json.dumps({
                "action": "ask_field",
                "field_name": "description",
                "field_value": None,
                "next_question": "Let me help break this down. Which service should change first?",
                "confidence": 0.85,
                "reasoning": "Complex multi-repo request analyzed",
                "user_skill": "developer",
            }),
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 100, "output_tokens": 200},
            cost_estimate=0.01,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=frontier_response,
        ) as mock_call:
            result = await escalate_to_frontier(
                "I need changes across 3 repos for the new auth system",
                [],
                {},
                escalation_reason="Multi-repo architectural request",
            )
        assert result.action == "ask_field"
        assert result.user_skill == "developer"
        # Verify frontier tier was used
        call_kwargs = mock_call.call_args
        assert call_kwargs[1]["tier"] == ModelTier.FRONTIER

    @pytest.mark.asyncio
    async def test_escalation_fallback_on_frontier_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "frontier failed"),
        ):
            result = await escalate_to_frontier(
                "complex request",
                [],
                {},
                escalation_reason="test",
            )
        assert result.action == "clarify"
        assert result.confidence == 0.0
        assert "complex" in result.next_question.lower() or "break it down" in result.next_question.lower()

    @pytest.mark.asyncio
    async def test_mini_escalate_action_triggers_frontier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When mini returns action='escalate', classify_intake_message calls frontier."""
        _make_settings(monkeypatch)
        # Mini model says escalate
        mini_response = _mock_openrouter_response({
            "action": "escalate",
            "confidence": 0.8,
            "reasoning": "Multi-repo request needs senior analysis",
        })
        # Frontier model gives a useful response
        frontier_response = OpenRouterResponse(
            content=json.dumps({
                "action": "ask_field",
                "field_name": "repo",
                "field_value": None,
                "next_question": "Which repo should we start with?",
                "confidence": 0.9,
                "reasoning": "Analyzed complex request",
                "user_skill": "developer",
            }),
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 100, "output_tokens": 200},
            cost_estimate=0.01,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=[mini_response, frontier_response],
        ) as mock_call:
            result = await classify_intake_message(
                "I need changes across auth-service, user-service, and the gateway",
                [], {},
            )
        # Should have called MINI then FRONTIER
        assert mock_call.call_count == 2
        assert mock_call.call_args_list[0][1]["tier"] == ModelTier.MINI
        assert mock_call.call_args_list[1][1]["tier"] == ModelTier.FRONTIER
        assert result.action == "ask_field"


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


class TestContextGathering:
    @pytest.mark.asyncio
    async def test_context_gathered_from_repo_indexer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_client = MagicMock()
        mock_client.suggest_repos_and_branches.return_value = {
            "repos": [{"full_name": "org/app", "description": "Main app"}],
            "branches": {"org/app": ["main", "develop"]},
        }
        with patch(
            "app.services.github_connection.check_github_connection",
            new_callable=AsyncMock,
            return_value=_connected_github_status(),
        ), patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=mock_client,
        ):
            context = await _gather_intake_context("U123")
        assert context["repos"] == [{"full_name": "org/app", "description": "Main app"}]
        assert context["branches"] == {"org/app": ["main", "develop"]}

    @pytest.mark.asyncio
    async def test_context_falls_back_to_github_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_gh = MagicMock()
        mock_gh.list_repos.return_value = [{"full_name": "org/fallback-repo"}]
        with patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=None,
        ), patch(
            "app.services.github_adapter.get_github_adapter",
            return_value=mock_gh,
        ):
            context = await _gather_intake_context()
        assert context["repos"] == [{"full_name": "org/fallback-repo"}]

    @pytest.mark.asyncio
    async def test_context_returns_none_when_both_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            side_effect=Exception("indexer down"),
        ), patch(
            "app.services.github_adapter.get_github_adapter",
            side_effect=Exception("github down"),
        ):
            context = await _gather_intake_context()
        assert context["repos"] is None

    @pytest.mark.asyncio
    async def test_user_history_included_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        fake_history = [{"title": "Past feature", "repo": "org/app", "status": "MERGED", "created_at": "2026-01-01"}]
        with patch(
            "app.services.github_connection.check_github_connection",
            new_callable=AsyncMock,
            return_value=_connected_github_status(),
        ), patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=None,
        ), patch(
            "app.services.github_adapter.get_github_adapter",
            side_effect=Exception("no github"),
        ), patch.object(
            __import__("app.services.intake_router", fromlist=["_get_user_history"]),
            "_get_user_history",
            new_callable=AsyncMock,
            return_value=fake_history,
        ):
            context = await _gather_intake_context("U123")
        assert context["user_history"] == fake_history


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_slack_user_id_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling classify_intake_message without slack_user_id must not crash."""
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "title",
            "field_value": "Test feature",
            "confidence": 0.9,
            "reasoning": "Clear request",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            # Call WITHOUT slack_user_id — must not raise
            result = await classify_intake_message("I need a test feature", [], {})
        assert result.action == "ask_field"

    @pytest.mark.asyncio
    async def test_positional_args_still_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Original 3-arg calling convention still works."""
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "confirm",
            "confidence": 0.95,
            "reasoning": "All fields done",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message(
                "looks good",
                [{"role": "user", "text": "hello"}],
                {"title": "X", "repo": "a/b"},
            )
        assert result.action == "confirm"


# ---------------------------------------------------------------------------
# GitHub connection integration
# ---------------------------------------------------------------------------


class TestGitHubConnectionIntegration:
    @pytest.mark.asyncio
    async def test_github_check_runs_before_repo_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GitHub connection check is called when slack_user_id is provided."""
        _make_settings(monkeypatch)
        call_order = []

        async def mock_check(user_id, **kw):
            call_order.append("github_check")
            return _connected_github_status()

        mock_client = MagicMock()
        def mock_suggest(**kw):
            call_order.append("repo_indexer")
            return {"repos": [{"full_name": "org/app"}], "branches": {}}
        mock_client.suggest_repos_and_branches = mock_suggest

        with patch(
            "app.services.github_connection.check_github_connection",
            side_effect=mock_check,
        ), patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=mock_client,
        ):
            context = await _gather_intake_context("U123")

        assert call_order == ["github_check", "repo_indexer"]
        assert context["github_status"].status == GitHubConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_repos_not_fetched_when_token_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When GitHub token is expired, repos are NOT fetched."""
        _make_settings(monkeypatch)
        expired_status = GitHubConnectionCheck(
            status=GitHubConnectionStatus.EXPIRED,
            username="testuser",
            repos_available=False,
            message="Token expired",
        )
        mock_client = MagicMock()

        with patch(
            "app.services.github_connection.check_github_connection",
            new_callable=AsyncMock,
            return_value=expired_status,
        ), patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=mock_client,
        ) as mock_indexer:
            context = await _gather_intake_context("U123")

        assert context["repos"] is None
        assert context["github_status"].status == GitHubConnectionStatus.EXPIRED
        # Repo indexer should NOT have been called
        mock_client.suggest_repos_and_branches.assert_not_called()

    @pytest.mark.asyncio
    async def test_repos_fetched_when_token_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When GitHub token is valid, repos ARE fetched."""
        _make_settings(monkeypatch)
        mock_client = MagicMock()
        mock_client.suggest_repos_and_branches.return_value = {
            "repos": [{"full_name": "org/app"}],
            "branches": {},
        }

        with patch(
            "app.services.github_connection.check_github_connection",
            new_callable=AsyncMock,
            return_value=_connected_github_status(),
        ), patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=mock_client,
        ):
            context = await _gather_intake_context("U123")

        assert context["repos"] == [{"full_name": "org/app"}]
        mock_client.suggest_repos_and_branches.assert_called_once()

    @pytest.mark.asyncio
    async def test_special_field_github_reauth_not_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """field_name='github_reauth' should have field_value cleared."""
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "github_reauth",
            "field_value": "should_be_cleared",
            "next_question": "Please reconnect your GitHub account.",
            "confidence": 0.9,
            "reasoning": "Token expired",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("I want to add a feature", [], {})
        assert result.field_name == "github_reauth"
        assert result.field_value is None  # Cleared by special field handling

    @pytest.mark.asyncio
    async def test_special_field_github_connect_not_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """field_name='github_connect' should have field_value cleared."""
        _make_settings(monkeypatch)
        mock_response = _mock_openrouter_response({
            "action": "ask_field",
            "field_name": "github_connect",
            "field_value": "should_be_cleared",
            "next_question": "Connect your GitHub to pick a repo.",
            "confidence": 0.9,
            "reasoning": "No connection",
        })
        with patch("app.services.openrouter_provider.call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_intake_message("build something", [], {})
        assert result.field_name == "github_connect"
        assert result.field_value is None

    @pytest.mark.asyncio
    async def test_context_works_without_slack_user_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without slack_user_id, no GitHub check runs, repos still fetched."""
        _make_settings(monkeypatch)
        mock_client = MagicMock()
        mock_client.suggest_repos_and_branches.return_value = {
            "repos": [{"full_name": "org/app"}],
            "branches": {},
        }

        with patch(
            "app.services.repo_indexer_adapter.get_repo_indexer_client",
            return_value=mock_client,
        ):
            # No slack_user_id → no github check → repos fetched normally
            context = await _gather_intake_context()

        assert context["github_status"] is None
        assert context["repos"] == [{"full_name": "org/app"}]
