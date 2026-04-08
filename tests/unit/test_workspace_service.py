from __future__ import annotations

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


def test_prepare_workspace_copies_local_reference_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_copy_root = tmp_path / "local-root"
    source_repo = local_copy_root / "samples" / "reuse_seed"
    source_repo.mkdir(parents=True)
    (source_repo / "README.md").write_text("seed fixture\n", encoding="utf-8")
    (source_repo / ".git").mkdir()
    (source_repo / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace-root"
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKSPACE_LOCAL_COPY_ROOT", str(local_copy_root))
    monkeypatch.setenv("WORKSPACE_ENABLE_GIT_CLONE", "false")

    spec = {
        "implementation_mode": "reuse_existing",
        "source_repos": ["samples/reuse_seed"],
    }

    result = prepare_workspace("2e6f9012-4f7d-4ff8-b81e-eeab42365e8a", spec)

    prepared = [r for r in result.prepared_references if r.status == "prepared" and r.method == "local_copy"]
    assert len(prepared) == 1

    snapshot_path = Path(prepared[0].destination)
    assert snapshot_path.exists()
    assert (snapshot_path / "README.md").read_text(encoding="utf-8") == "seed fixture\n"
    assert not (snapshot_path / ".git").exists()

