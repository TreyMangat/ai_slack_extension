from __future__ import annotations

from app.services.url_safety import normalize_external_url, normalize_external_url_list


def test_normalize_external_url_allows_https_only() -> None:
    assert normalize_external_url("https://example.com/path") == "https://example.com/path"
    assert normalize_external_url("javascript:alert(1)") == ""


def test_normalize_external_url_list_filters_invalid_entries() -> None:
    values = normalize_external_url_list(["https://example.com", "ftp://bad.example.com", ""])
    assert values == ["https://example.com"]

