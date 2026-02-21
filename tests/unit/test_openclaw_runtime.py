from __future__ import annotations

import json
from pathlib import Path

from app.services.openclaw_runtime import stage_openclaw_auth_if_needed


class _StubSettings:
    def __init__(self, *, runtime_dir: Path, seed_dir: Path) -> None:
        self.mock_mode = False
        self.openclaw_auth_dir = str(runtime_dir)
        self.openclaw_auth_seed_dir = str(seed_dir)

    def coderunner_mode_normalized(self) -> str:
        return "opencode"

    def opencode_execution_mode_normalized(self) -> str:
        return "local_openclaw"


def _write_seed_auth(seed_dir: Path, *, workspace: str) -> None:
    auth_file = seed_dir / "agents" / "main" / "agent" / "auth.json"
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text('{"ok":true}\n', encoding="utf-8")
    config_path = seed_dir / "openclaw.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"workspace": workspace}}}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_stage_openclaw_auth_normalizes_windows_workspace(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    seed_dir = tmp_path / "seed"
    _write_seed_auth(seed_dir, workspace=r"C:\Users\trey2\.openclaw\workspace")

    result = stage_openclaw_auth_if_needed(_StubSettings(runtime_dir=runtime_dir, seed_dir=seed_dir))

    assert result["required"] is True
    assert result["staged"] is True
    assert result["workspace_normalized"] is True
    assert result["workspace_path"] == "/tmp/openclaw_workspace"

    config_payload = json.loads((runtime_dir / "openclaw.json").read_text(encoding="utf-8"))
    assert config_payload["agents"]["defaults"]["workspace"] == "/tmp/openclaw_workspace"


def test_stage_openclaw_auth_keeps_unix_workspace(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    seed_dir = tmp_path / "seed"
    _write_seed_auth(seed_dir, workspace="/home/app/.openclaw/workspace")

    result = stage_openclaw_auth_if_needed(_StubSettings(runtime_dir=runtime_dir, seed_dir=seed_dir))

    assert result["required"] is True
    assert result["staged"] is True
    assert result["workspace_normalized"] is False
    assert result["workspace_path"] == "/home/app/.openclaw/workspace"
