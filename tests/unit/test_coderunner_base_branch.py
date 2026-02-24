from __future__ import annotations

from pathlib import Path

import pytest

import app.services.coderunner_adapter as coderunner_mod
from app.config import Settings


def test_prepare_target_repo_clone_uses_base_branch_when_provided(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(
        cmd: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 600,
        env: dict[str, str] | None = None,
    ) -> str:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return ""

    monkeypatch.setattr(coderunner_mod, "_run_command", fake_run_command)
    monkeypatch.setattr(coderunner_mod, "_github_basic_auth_header", lambda _token: "AUTHORIZATION: basic stub")

    target = tmp_path / "target"
    resolved = coderunner_mod._prepare_target_repo(
        target_path=target,
        owner="acme",
        repo="widgets",
        token="token",
        base_branch="develop",
    )

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--branch" in cmd
    branch_index = cmd.index("--branch")
    assert cmd[branch_index + 1] == "develop"
    assert resolved == "develop"


def test_prepare_target_repo_returns_checked_out_branch_when_base_not_provided(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(
        cmd: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 600,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return "master"
        return ""

    monkeypatch.setattr(coderunner_mod, "_run_command", fake_run_command)
    monkeypatch.setattr(coderunner_mod, "_github_basic_auth_header", lambda _token: "AUTHORIZATION: basic stub")

    target = tmp_path / "target"
    resolved = coderunner_mod._prepare_target_repo(
        target_path=target,
        owner="acme",
        repo="widgets",
        token="token",
        base_branch="",
    )

    assert resolved == "master"
    assert any(cmd[:3] == ["git", "rev-parse", "--abbrev-ref"] for cmd in calls)


def test_prepare_target_repo_missing_base_branch_returns_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_command(
        cmd: list[str],
        *,
        cwd: Path,
        timeout_seconds: int = 600,
        env: dict[str, str] | None = None,
    ) -> str:
        raise RuntimeError("fatal: Remote branch no-such-branch not found in upstream origin")

    monkeypatch.setattr(coderunner_mod, "_run_command", fake_run_command)
    monkeypatch.setattr(coderunner_mod, "_github_basic_auth_header", lambda _token: "AUTHORIZATION: basic stub")

    target = tmp_path / "target"
    with pytest.raises(RuntimeError) as exc:
        coderunner_mod._prepare_target_repo(
            target_path=target,
            owner="acme",
            repo="widgets",
            token="token",
            base_branch="no-such-branch",
        )

    message = str(exc.value)
    assert "does not exist" in message
    assert "no-such-branch" in message


def test_resolve_pr_base_branch_returns_empty_when_unspecified() -> None:
    settings = Settings.model_construct(github_default_branch="main")
    resolved = coderunner_mod._resolve_pr_base_branch(spec={"base_branch": ""}, settings=settings)  # noqa: SLF001
    assert resolved == ""
