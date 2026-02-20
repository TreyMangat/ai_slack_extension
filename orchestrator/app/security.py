from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import get_settings


def require_api_auth(x_ff_token: str | None = Header(default=None, alias="X-FF-Token")) -> None:
    """Optional API auth gate.

    If API_AUTH_TOKEN is unset, auth is disabled for local/dev convenience.
    """

    settings = get_settings()
    required = settings.api_auth_token.strip()
    if not required:
        return

    if not x_ff_token or x_ff_token.strip() != required:
        raise HTTPException(status_code=401, detail="Missing or invalid API auth token")
