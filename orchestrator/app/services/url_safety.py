from __future__ import annotations

from urllib.parse import urlparse


ALLOWED_EXTERNAL_SCHEMES = {"http", "https"}


def normalize_external_url(value: str) -> str:
    """Return a safe external URL or an empty string.

    We only allow absolute http/https URLs so user-provided links cannot
    execute javascript: payloads from rendered templates.
    """

    raw = (value or "").strip()
    if not raw:
        return ""

    try:
        parsed = urlparse(raw)
    except Exception:
        return ""

    if parsed.scheme.lower() not in ALLOWED_EXTERNAL_SCHEMES:
        return ""
    if not parsed.netloc:
        return ""
    return raw


def normalize_external_url_list(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        safe = normalize_external_url(str(value))
        if safe:
            out.append(safe)
    return out

