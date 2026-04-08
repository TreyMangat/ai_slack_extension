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


def _set_base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("WORKSPACE_ENABLE_GIT_CLONE", "false")


def test_redact_clone_url_strips_credentials() -> None:
    raw = "https://x-access-token:secret-token@github.com/org/repo.git"
    redacted = redact_clone_url_for_logging(raw)
    assert "secret-token" not in redacted
    assert redacted == "https://github.com/org/repo.git"


def test_prepare_workspace_copies_local_reference_from_allowed_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_base_env(monkeypatch, tmp_path)

    local_copy_root = tmp_path / "allowed"
    source_repo = local_copy_root / "sample_repo"
    source_repo.mkdir(parents=True)
    (source_repo / "README.md").write_text("seed repo\n", encoding="utf-8")

    monkeypatch.setenv("WORKSPACE_LOCAL_COPY_ROOT", str(local_copy_root))

    result = prepare_workspace(
        "05588d92-8a1b-4532-adb0-5937530e0633",
        {
            "implementation_mode": "reuse_existing",
            "source_repos": [str(source_repo)],
            "repo": "",
        },
    )

    assert len(result.prepared_references) == 1
    prepared = result.prepared_references[0]
    assert prepared.status == "prepared"
    assert prepared.method == "local_copy"
    assert (Path(prepared.destination) / "README.md").read_text(encoding="utf-8") == "seed repo\n"


def test_prepare_workspace_copies_from_workspace_reference_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_base_env(monkeypatch, tmp_path)

    # Simulate orchestrator-provided local snapshot under WORKSPACE_ROOT/references
    seeded_reference = tmp_path / "workspaces" / "references" / "01_app-app-samples-reuse_seed"
    seeded_reference.mkdir(parents=True)
    (seeded_reference / "README.md").write_text("prepared snapshot\n", encoding="utf-8")

    # Keep primary local copy root elsewhere to prove seed fallback path is used.
    monkeypatch.setenv("WORKSPACE_LOCAL_COPY_ROOT", str(tmp_path / "other-root"))

    result = prepare_workspace(
        "05588d92-8a1b-4532-adb0-5937530e0633",
        {
            "implementation_mode": "reuse_existing",
            "source_repos": [str(seeded_reference)],
            "repo": "",
        },
    )

    assert len(result.prepared_references) == 1
    prepared = result.prepared_references[0]
    assert prepared.status == "prepared"
    assert prepared.method == "local_copy"
    assert (Path(prepared.destination) / "README.md").read_text(encoding="utf-8") == "prepared snapshot\n"
