from __future__ import annotations

import shutil
import json
import re
from pathlib import Path
from typing import Any

from app.config import Settings


def _auth_file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(list(path.glob("agents/*/agent/auth*.json")))


def _looks_like_windows_path(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", text):
        return True
    return "\\" in text


def _normalize_workspace_setting(runtime_dir: Path) -> tuple[bool, str]:
    config_path = runtime_dir / "openclaw.json"
    if not config_path.exists():
        return False, ""

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False, ""

    agents = payload.get("agents") if isinstance(payload, dict) else None
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    workspace_value = defaults.get("workspace") if isinstance(defaults, dict) else ""
    workspace_text = str(workspace_value or "").strip()
    if workspace_text and not _looks_like_windows_path(workspace_text):
        return False, workspace_text

    normalized = "/tmp/openclaw_workspace"
    if not isinstance(payload, dict):
        payload = {}
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        payload["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    defaults["workspace"] = normalized

    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    Path(normalized).mkdir(parents=True, exist_ok=True)
    return True, normalized


def stage_openclaw_auth_if_needed(settings: Settings) -> dict[str, Any]:
    """Copy OpenClaw auth from read-only seed dir into writable runtime dir when needed."""

    if settings.mock_mode:
        return {"required": False, "staged": False, "reason": "mock_mode"}
    if settings.coderunner_mode_normalized() != "opencode":
        return {"required": False, "staged": False, "reason": "coderunner_mode"}
    if settings.opencode_execution_mode_normalized() != "local_openclaw":
        return {"required": False, "staged": False, "reason": "execution_mode"}

    runtime_dir = Path((settings.openclaw_auth_dir or "").strip() or "/home/app/.openclaw")
    seed_dir = Path((settings.openclaw_auth_seed_dir or "").strip() or "/run/secrets/openclaw")
    runtime_count = _auth_file_count(runtime_dir)
    if runtime_count > 0:
        return {
            "required": True,
            "staged": False,
            "runtime_dir": str(runtime_dir),
            "seed_dir": str(seed_dir),
            "auth_files": runtime_count,
        }

    seed_count = _auth_file_count(seed_dir)
    if seed_count == 0:
        return {
            "required": True,
            "staged": False,
            "runtime_dir": str(runtime_dir),
            "seed_dir": str(seed_dir),
            "auth_files": 0,
        }

    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    shutil.copytree(seed_dir, runtime_dir)

    # OpenClaw may chmod auth files; ensure copied files are writable in container FS.
    for path in [runtime_dir, *runtime_dir.rglob("*")]:
        try:
            if path.is_dir():
                path.chmod(0o700)
            else:
                path.chmod(0o600)
        except Exception:  # noqa: BLE001
            continue

    workspace_updated, workspace_path = _normalize_workspace_setting(runtime_dir)

    return {
        "required": True,
        "staged": True,
        "runtime_dir": str(runtime_dir),
        "seed_dir": str(seed_dir),
        "auth_files": _auth_file_count(runtime_dir),
        "workspace_normalized": workspace_updated,
        "workspace_path": workspace_path,
    }
