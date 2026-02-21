from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
from rich.console import Console

from app.config import get_settings
from app.services.github_auth import get_github_token_provider


console = Console()


@dataclass
class GitHubIssue:
    number: int
    html_url: str


class GitHubAdapter:
    async def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> GitHubIssue:
        raise NotImplementedError

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        raise NotImplementedError

    async def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> str:
        raise NotImplementedError

    async def update_pull_request_body(self, *, pr_number: int, body: str) -> None:
        raise NotImplementedError


class MockGitHubAdapter(GitHubAdapter):
    async def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> GitHubIssue:
        fake_number = int(time.time()) % 100000
        fake_url = f"https://example.local/github/issues/{fake_number}"
        console.print(f"[bold cyan][MOCK GitHub][/bold cyan] create_issue #{fake_number}: {title}")
        return GitHubIssue(number=fake_number, html_url=fake_url)

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        console.print(f"[bold cyan][MOCK GitHub][/bold cyan] comment_issue #{issue_number}: {body}")

    async def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> str:
        fake_number = int(time.time()) % 100000
        fake_url = f"https://example.local/github/pull/{fake_number}"
        console.print(
            f"[bold cyan][MOCK GitHub][/bold cyan] create_pull_request #{fake_number}: "
            f"{title} (head={head}, base={base})"
        )
        return fake_url

    async def update_pull_request_body(self, *, pr_number: int, body: str) -> None:
        console.print(f"[bold cyan][MOCK GitHub][/bold cyan] update_pull_request_body #{pr_number}")


class RealGitHubAdapter(GitHubAdapter):
    def __init__(self, *, owner: str, repo: str, api_base: str = "https://api.github.com"):
        self.owner = owner
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        self.token_provider = get_github_token_provider()
        self.max_attempts = 4
        self.initial_backoff_seconds = 0.5

    def _headers(self) -> dict[str, str]:
        token = self.token_provider.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code == 429 or code >= 500
        return False

    async def _request_with_retry(
        self,
        *,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> httpx.Response:
        delay = self.initial_backoff_seconds
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(method, url, headers=self._headers(), json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Retryable GitHub status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == self.max_attempts or not self._is_retryable(exc):
                    raise
                await asyncio.sleep(delay)
                delay *= 2.0
        if last_exc:
            raise last_exc
        raise RuntimeError("GitHub API retry loop exited unexpectedly")

    async def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> GitHubIssue:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/issues"
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels

        r = await self._request_with_retry(method="POST", url=url, payload=payload)
        data = r.json()
        return GitHubIssue(number=int(data["number"]), html_url=str(data["html_url"]))

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments"
        payload = {"body": body}

        await self._request_with_retry(method="POST", url=url, payload=payload)

    async def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> str:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        r = await self._request_with_retry(method="POST", url=url, payload=payload)
        data = r.json()
        return str(data.get("html_url", "")).strip()

    async def update_pull_request_body(self, *, pr_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        payload = {"body": body}
        await self._request_with_retry(method="PATCH", url=url, payload=payload)


def get_github_adapter() -> GitHubAdapter:
    settings = get_settings()

    if settings.mock_mode or not settings.github_enabled:
        return MockGitHubAdapter()

    if not (settings.github_repo_owner and settings.github_repo_name):
        console.print("[yellow]GitHub enabled but missing owner/repo. Falling back to mock adapter.[/yellow]")
        return MockGitHubAdapter()

    mode = settings.github_auth_mode_normalized()
    if mode in {"", "token", "pat"} and not settings.github_token:
        console.print("[yellow]GitHub token auth selected but GITHUB_TOKEN is empty. Falling back to mock adapter.[/yellow]")
        return MockGitHubAdapter()
    if mode == "app":
        if not settings.github_app_id or not settings.github_app_installation_id:
            console.print(
                "[yellow]GitHub App auth selected but app identifiers are missing. Falling back to mock adapter.[/yellow]"
            )
            return MockGitHubAdapter()
        if not settings.github_app_private_key and not settings.github_app_private_key_path:
            console.print(
                "[yellow]GitHub App auth selected but private key is missing. Falling back to mock adapter.[/yellow]"
            )
            return MockGitHubAdapter()
    if mode not in {"", "token", "pat", "app"}:
        console.print(f"[yellow]Unsupported GITHUB_AUTH_MODE={settings.github_auth_mode}. Falling back to mock.[/yellow]")
        return MockGitHubAdapter()

    return RealGitHubAdapter(
        owner=settings.github_repo_owner,
        repo=settings.github_repo_name,
        api_base=settings.github_api_base,
    )
