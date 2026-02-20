from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from rich.console import Console

from app.config import get_settings


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


class MockGitHubAdapter(GitHubAdapter):
    async def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> GitHubIssue:
        fake_number = int(time.time()) % 100000
        fake_url = f"https://example.local/github/issues/{fake_number}"
        console.print(f"[bold cyan][MOCK GitHub][/bold cyan] create_issue #{fake_number}: {title}")
        return GitHubIssue(number=fake_number, html_url=fake_url)

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        console.print(f"[bold cyan][MOCK GitHub][/bold cyan] comment_issue #{issue_number}: {body}")


class RealGitHubAdapter(GitHubAdapter):
    def __init__(self, *, token: str, owner: str, repo: str, api_base: str = "https://api.github.com"):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> GitHubIssue:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/issues"
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
            return GitHubIssue(number=int(data["number"]), html_url=str(data["html_url"]))

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments"
        payload = {"body": body}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()


def get_github_adapter() -> GitHubAdapter:
    settings = get_settings()

    if settings.mock_mode or not settings.github_enabled:
        return MockGitHubAdapter()

    if not (settings.github_token and settings.github_repo_owner and settings.github_repo_name):
        console.print("[yellow]GitHub enabled but missing token/owner/repo. Falling back to mock adapter.[/yellow]")
        return MockGitHubAdapter()

    return RealGitHubAdapter(
        token=settings.github_token,
        owner=settings.github_repo_owner,
        repo=settings.github_repo_name,
        api_base=settings.github_api_base,
    )
