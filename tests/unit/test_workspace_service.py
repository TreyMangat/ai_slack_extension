from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.services.workspace_service import prepare_workspace, redact_clone_url_for_logging


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_redact_clone_url_strips_credentials() -> None:
    raw = "https://x-access-token:secret-token@github.com/org/repo.git"
    redacted = redact_clone_url_for_logging(raw)
    assert "secret-token" not in redacted
    assert redacted == "https://github.com/org/repo.git"


def test_prepare_workspace_local_copy_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_copy_root = tmp_path / "fixtures"
    source_repo = local_copy_root / "reuse_seed"
    source_repo.mkdir(parents=True)
    (source_repo / "README.md").write_text("seed fixture", encoding="utf-8")
    (source_repo / ".git").mkdir()
    (source_repo / ".git" / "config").write_text("[core]", encoding="utf-8")

    workspace_root = tmp_path / "workspaces"

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKSPACE_LOCAL_COPY_ROOT", str(local_copy_root))
    monkeypatch.setenv("WORKSPACE_ENABLE_GIT_CLONE", "false")

    result = prepare_workspace(
        "bfc77003-5e8b-44dc-90c4-8c06fcb0a793",
        {
            "implementation_mode": "reuse_existing",
            "source_repos": [str(source_repo)],
        },
    )

    assert len(result.prepared_references) == 1
    prepared = result.prepared_references[0]
    assert prepared.method == "local_copy"
    assert prepared.status == "prepared"
    destination = Path(prepared.destination)
    assert destination.exists()
    assert (destination / "README.md").read_text(encoding="utf-8") == "seed fixture"
    assert not (destination / ".git").exists()

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    prepared_manifest = manifest["workspace"]["prepared_references"]
    assert len(prepared_manifest) == 1
    assert prepared_manifest[0]["method"] == "local_copy"
    assert prepared_manifest[0]["status"] == "prepared"

