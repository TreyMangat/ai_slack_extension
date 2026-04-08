from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.services.workspace_service import prepare_workspace, redact_clone_url_for_logging


def test_redact_clone_url_strips_credentials() -> None:
    raw = "https://x-access-token:secret-token@github.com/org/repo.git"
    redacted = redact_clone_url_for_logging(raw)
    assert "secret-token" not in redacted
    assert redacted == "https://github.com/org/repo.git"


def test_prepare_workspace_copies_local_snapshot_reference(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspaces"
    local_copy_root = tmp_path / "local_refs"
    fixture_repo = local_copy_root / "samples" / "reuse_seed"
    fixture_repo.mkdir(parents=True)
    (fixture_repo / "README.md").write_text("seed fixture\n", encoding="utf-8")
    (fixture_repo / ".git").mkdir()
    (fixture_repo / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKSPACE_LOCAL_COPY_ROOT", str(local_copy_root))
    monkeypatch.setenv("WORKSPACE_ENABLE_GIT_CLONE", "false")
    get_settings.cache_clear()

    result = prepare_workspace(
        feature_id="7046648f-ae65-4199-b5c6-2012e893ef22",
        spec={
            "implementation_mode": "reuse_existing",
            "source_repos": ["samples/reuse_seed"],
        },
    )

    assert len(result.prepared_references) == 1
    ref = result.prepared_references[0]
    assert ref.status == "prepared"
    assert ref.method == "local_copy"

    destination = Path(ref.destination)
    assert destination.exists()
    assert (destination / "README.md").read_text(encoding="utf-8") == "seed fixture\n"
    assert not (destination / ".git").exists()

    get_settings.cache_clear()

