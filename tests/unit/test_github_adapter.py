from __future__ import annotations

import asyncio

import httpx

from app.services.github_adapter import RealGitHubAdapter


def _http_422_response(url: str, payload: dict[str, object]) -> httpx.Response:
    request = httpx.Request("POST", url)
    return httpx.Response(422, request=request, json=payload)


def test_create_pull_request_returns_existing_open_pr_when_github_reports_already_exists(monkeypatch) -> None:
    adapter = RealGitHubAdapter(owner="acme", repo="widgets")
    pulls_url = "https://api.github.com/repos/acme/widgets/pulls"

    async def fake_request_with_retry(
        *,
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        if method == "POST":
            response = _http_422_response(
                url,
                {
                    "message": "Validation Failed",
                    "errors": [
                        {
                            "resource": "PullRequest",
                            "code": "custom",
                            "message": "A pull request already exists for acme:branch-123.",
                        }
                    ],
                },
            )
            raise httpx.HTTPStatusError("Validation Failed", request=response.request, response=response)
        if method == "GET":
            assert url == pulls_url
            assert params == {
                "state": "open",
                "head": "acme:branch-123",
                "base": "main",
                "per_page": 1,
            }
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=[{"html_url": "https://github.com/acme/widgets/pull/42"}],
            )
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(adapter, "_request_with_retry", fake_request_with_retry)

    pr_url = asyncio.run(
        adapter.create_pull_request(
            title="Test PR",
            body="body",
            head="branch-123",
            base="main",
        )
    )

    assert pr_url == "https://github.com/acme/widgets/pull/42"


def test_create_pull_request_surfaces_validation_details_for_422(monkeypatch) -> None:
    adapter = RealGitHubAdapter(owner="acme", repo="widgets")

    async def fake_request_with_retry(
        *,
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
    ) -> httpx.Response:
        assert method == "POST"
        response = _http_422_response(
            url,
            {
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequest",
                        "field": "head",
                        "code": "custom",
                        "message": "No commits between main and branch-123.",
                    }
                ],
            },
        )
        raise httpx.HTTPStatusError("Validation Failed", request=response.request, response=response)

    monkeypatch.setattr(adapter, "_request_with_retry", fake_request_with_retry)

    try:
        asyncio.run(
            adapter.create_pull_request(
                title="Test PR",
                body="body",
                head="branch-123",
                base="main",
            )
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        text = str(exc)
        assert "head=`branch-123`" in text
        assert "base=`main`" in text
        assert "No commits between main and branch-123." in text
