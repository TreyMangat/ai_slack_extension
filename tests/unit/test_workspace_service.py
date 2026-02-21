from __future__ import annotations

from app.services.workspace_service import redact_clone_url_for_logging


def test_redact_clone_url_strips_credentials() -> None:
    raw = "https://x-access-token:secret-token@github.com/org/repo.git"
    redacted = redact_clone_url_for_logging(raw)
    assert "secret-token" not in redacted
    assert redacted == "https://github.com/org/repo.git"

