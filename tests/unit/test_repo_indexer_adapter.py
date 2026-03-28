from __future__ import annotations

import httpx

from app.config import Settings
import app.services.repo_indexer_adapter as indexer_mod
from app.services.repo_indexer_adapter import RepoIndexerClient, get_repo_indexer_client


def test_get_repo_indexer_client_returns_none_without_base_url() -> None:
    settings = Settings.model_construct(indexer_base_url="")
    assert get_repo_indexer_client(settings=settings) is None


def test_search_falls_back_to_legacy_search_endpoint(monkeypatch) -> None:
    calls: list[str] = []

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            _ = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = exc_type
            _ = exc
            _ = tb
            return False

        def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> httpx.Response:  # noqa: A002
            _ = json
            _ = headers
            calls.append(url)
            request = httpx.Request("POST", url)
            if url.endswith("/api/indexer/search"):
                return httpx.Response(404, request=request, json={"detail": "not found"})
            return httpx.Response(200, request=request, json={"query": "retry logic", "results": []})

    monkeypatch.setattr(indexer_mod.httpx, "Client", DummyClient)

    client = RepoIndexerClient(base_url="http://indexer.local", timeout_seconds=1.0)
    payload = client.search(query="retry logic")

    assert payload["query"] == "retry logic"
    assert calls[0].endswith("/api/indexer/search")
    assert calls[1].endswith("/search")


def test_suggest_includes_auth_headers(monkeypatch) -> None:
    captured_headers: dict[str, str] = {}

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args
            _ = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            _ = exc_type
            _ = exc
            _ = tb
            return False

        def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> httpx.Response:  # noqa: A002
            _ = url
            _ = json
            captured_headers.update(headers)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"actor_id": "github-user", "results": [], "auth_required": False},
            )

    monkeypatch.setattr(indexer_mod.httpx, "Client", DummyClient)

    settings = Settings.model_construct(
        indexer_base_url="http://indexer.local",
        indexer_auth_token="secret-token",
        indexer_timeout_seconds=2,
    )
    client = RepoIndexerClient.from_settings(settings)
    payload = client.suggest_repos_and_branches(actor_id="github-user", query="payments")

    assert payload["actor_id"] == "github-user"
    assert captured_headers["Authorization"] == "Bearer secret-token"
    assert captured_headers["X-FF-Token"] == "secret-token"
