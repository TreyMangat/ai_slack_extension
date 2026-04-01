"""Tests for tasks/jobs.py build orchestration helpers and policies."""
from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import FeatureRequest
from app.services.workspace_service import PreparedReference, WorkspacePreparationResult
from app.state_machine import BUILDING, FAILED_BUILD
from app.tasks.jobs import (
    _build_heartbeat_loop,
    _build_timeout_window_seconds,
    _feature_reference,
    _format_duration_seconds,
    _mode_strategy_label,
    _truncate_for_event,
    _workspace_plan,
    kickoff_build,
    kickoff_build_job,
)


class _SettingsStub:
    def __init__(
        self,
        *,
        coderunner_mode: str = "opencode",
        execution_mode: str = "local_openclaw",
        timeout_seconds: int = 1800,
        mock_mode: bool = True,
        github_enabled: bool = True,
    ) -> None:
        self._coderunner_mode = coderunner_mode
        self._execution_mode = execution_mode
        self.opencode_timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode
        self.github_enabled = github_enabled
        self.build_status_heartbeat_seconds = 0

    def coderunner_mode_normalized(self) -> str:
        return self._coderunner_mode

    def opencode_execution_mode_normalized(self) -> str:
        return self._execution_mode


class _DbSessionContext:
    def __init__(self, db: MagicMock) -> None:
        self._db = db

    def __enter__(self) -> MagicMock:
        return self._db

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _workspace_result(*, source_repos: list[str] | None = None) -> WorkspacePreparationResult:
    return WorkspacePreparationResult(
        workspace_id="ff-feature-123-1234567890",
        workspace_root="/tmp/workspaces",
        workspace_path="/tmp/workspaces/ff-feature-123-1234567890",
        target_path="/tmp/workspaces/ff-feature-123-1234567890/target",
        references_path="/tmp/workspaces/ff-feature-123-1234567890/references",
        manifest_path="/tmp/workspaces/ff-feature-123-1234567890/workspace_manifest.json",
        implementation_mode="reuse_existing",
        target_repo="org/repo",
        source_repos=source_repos or ["org/reference"],
        prepared_references=[
            PreparedReference(
                source="org/reference",
                destination="/tmp/workspaces/ff-feature-123-1234567890/references/01_org-reference",
                method="git_clone",
                status="prepared",
                detail="snapshot cloned",
            )
        ],
        errors=[],
    )


def test_truncate_for_event_returns_short_value_unchanged():
    assert _truncate_for_event("hello", max_chars=100) == "hello"


def test_truncate_for_event_shortens_long_value():
    result = _truncate_for_event("x" * 500, max_chars=100)
    assert len(result) == 100
    assert result.endswith("...")


def test_format_duration_seconds_covers_seconds_minutes_and_hours():
    assert _format_duration_seconds(0) == "0s"
    assert _format_duration_seconds(125) == "2m 5s"
    assert _format_duration_seconds(3661) == "1h 1m 1s"


def test_build_timeout_window_seconds_uses_openclaw_timeout():
    settings = _SettingsStub(timeout_seconds=2400)
    assert _build_timeout_window_seconds(settings) == 2400


def test_build_timeout_window_seconds_returns_zero_for_other_modes():
    settings = _SettingsStub(coderunner_mode="native_llm", execution_mode="remote", timeout_seconds=2400)
    assert _build_timeout_window_seconds(settings) == 0


def test_mode_strategy_label_varies_by_mode():
    assert _mode_strategy_label("new_feature") == "target repo only"
    assert _mode_strategy_label("reuse_existing") == "reuse reference snapshots"


def test_feature_reference_uses_slug_and_short_id():
    feature = FeatureRequest(
        id="12345678-abcd-efgh",
        status=BUILDING,
        title="Add button to header",
        requester_user_id="U_TEST",
        spec={},
    )
    assert _feature_reference(feature) == "add-button-to-12345678"


def test_workspace_plan_returns_base_configuration():
    plan = _workspace_plan(
        {
            "implementation_mode": "new_feature",
            "source_repos": ["org/reference", "  "],
        },
        "feature-123",
        github_actor_id="U_TEST",
        slack_team_id="T_TEST",
    )

    assert plan["workspace_id"] == "ff-feature-123"
    assert plan["implementation_mode"] == "new_feature"
    assert plan["source_repos"] == ["org/reference"]
    assert plan["github_actor_id"] == "U_TEST"
    assert plan["slack_team_id"] == "T_TEST"
    assert "workspace_snapshot" not in plan


def test_workspace_plan_includes_snapshot_details():
    workspace = _workspace_result()
    plan = _workspace_plan(
        {"implementation_mode": "reuse_existing", "source_repos": ["org/reference"]},
        "feature-123",
        workspace=workspace,
    )

    assert plan["workspace_snapshot"]["workspace_path"] == workspace.workspace_path
    assert plan["workspace_snapshot"]["target_path"] == workspace.target_path
    assert plan["workspace_snapshot"]["prepared_references"][0]["status"] == "prepared"


@pytest.mark.asyncio
async def test_build_heartbeat_loop_posts_progress_message():
    slack = MagicMock()
    slack.post_thread_message.side_effect = asyncio.CancelledError()

    with (
        patch("app.tasks.jobs.asyncio.sleep", new=AsyncMock(return_value=None)),
        pytest.raises(asyncio.CancelledError),
    ):
        await _build_heartbeat_loop(
            slack=slack,
            channel_id="C_TEST",
            thread_ts="123.456",
            team_id="T_TEST",
            feature_title="Test feature",
            mode="new_feature",
            target_repo="org/repo",
            started_at=datetime.utcnow(),
            interval_seconds=5,
            timeout_window_seconds=1800,
        )

    slack.post_thread_message.assert_called_once()
    message = slack.post_thread_message.call_args.kwargs["text"]
    assert "Still building *Test feature*" in message
    assert "Repo: `org/repo`" in message
    assert "Timeout window:" in message


def test_kickoff_build_job_calls_asyncio_run():
    with (
        patch("app.tasks.jobs.kickoff_build", new=MagicMock(return_value="coroutine-token")) as kickoff_mock,
        patch("asyncio.run") as run_mock,
    ):
        kickoff_build_job("test-feature-id")

    kickoff_mock.assert_called_once_with("test-feature-id")
    run_mock.assert_called_once_with("coroutine-token")


@pytest.mark.asyncio
async def test_kickoff_build_returns_when_feature_missing():
    db = MagicMock()
    db.get.return_value = None

    with (
        patch("app.tasks.jobs.get_settings", return_value=MagicMock()),
        patch("app.tasks.jobs.get_coderunner_adapter", return_value=MagicMock()),
        patch("app.tasks.jobs.get_slack_adapter", return_value=MagicMock()),
        patch("app.tasks.jobs.db_session", return_value=_DbSessionContext(db)),
        patch("app.tasks.jobs.logger.error") as logger_error_mock,
    ):
        await kickoff_build("missing-feature")

    logger_error_mock.assert_called_once_with("build_feature_not_found feature_id=%s", "missing-feature")


@pytest.mark.asyncio
async def test_kickoff_build_marks_failed_when_github_disabled_in_non_mock_mode():
    feature = FeatureRequest(
        id="feature-123",
        status=BUILDING,
        title="Test feature",
        requester_user_id="U_TEST",
        slack_team_id="",
        slack_channel_id="",
        slack_thread_ts="",
        active_build_job_id="",
        spec={"title": "Test feature", "implementation_mode": "new_feature"},
    )
    db = MagicMock()
    db.get.return_value = feature

    settings = _SettingsStub(mock_mode=False, github_enabled=False)
    workspace = _workspace_result(source_repos=[])

    with (
        patch("app.tasks.jobs.get_settings", return_value=settings),
        patch("app.tasks.jobs.get_coderunner_adapter", return_value=MagicMock()),
        patch("app.tasks.jobs.get_slack_adapter", return_value=MagicMock()),
        patch("app.tasks.jobs.db_session", return_value=_DbSessionContext(db)),
        patch("app.tasks.jobs.prepare_workspace", return_value=workspace),
        patch("app.tasks.jobs.log_event") as log_event_mock,
        patch("app.tasks.jobs.metrics.inc") as metrics_inc_mock,
    ):
        await kickoff_build(feature.id)

    assert feature.status == FAILED_BUILD
    assert feature.active_build_job_id == ""
    assert "GitHub integration must be enabled" in feature.last_error
    assert any(call.kwargs["event_type"] == "build_failed" for call in log_event_mock.call_args_list)
    assert metrics_inc_mock.call_args_list[0].args == ("build_jobs_started_total", 1)
    assert metrics_inc_mock.call_args_list[-1].args == ("build_jobs_failed_total", 1)


def test_enqueue_has_timeout_policy():
    required_keywords = {"job_timeout", "result_ttl", "failure_ttl"}

    for filepath in [
        "orchestrator/app/api/routes/api.py",
        "orchestrator/app/api/routes/ui.py",
    ]:
        source = pathlib.Path(filepath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        enqueue_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "enqueue"
        ]
        assert enqueue_calls, f"{filepath} does not enqueue build jobs"
        for call in enqueue_calls:
            keywords = {kw.arg for kw in call.keywords if kw.arg}
            missing = required_keywords - keywords
            assert not missing, f"{filepath} enqueue missing {sorted(missing)}"
