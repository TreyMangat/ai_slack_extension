from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.services.github_auth import get_github_token_provider

logger = logging.getLogger(__name__)


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
        logger.info("mock_github_create_issue issue_number=%s title=%s", fake_number, title)
        return GitHubIssue(number=fake_number, html_url=fake_url)

    async def comment_issue(self, *, issue_number: int, body: str) -> None:
        logger.info("mock_github_comment_issue issue_number=%s body=%s", issue_number, body)

    async def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> str:
        fake_number = int(time.time()) % 100000
        fake_url = f"https://example.local/github/pull/{fake_number}"
        logger.info(
            "mock_github_create_pull_request pr_number=%s title=%s head=%s base=%s",
            fake_number,
            title,
            head,
            base,
        )
        return fake_url

    async def update_pull_request_body(self, *, pr_number: int, body: str) -> None:
        logger.info("mock_github_update_pull_request_body pr_number=%s", pr_number)


class RealGitHubAdapter(GitHubAdapter):
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        api_base: str = "https://api.github.com",
        actor_id: str = "",
        team_id: str = "",
    ):
        self.owner = owner
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        self.actor_id = (actor_id or "").strip()
        self.team_id = (team_id or "").strip()
        self.token_provider = get_github_token_provider()
        self.max_attempts = 4
        self.initial_backoff_seconds = 0.5

    def _headers(self) -> dict[str, str]:
        token = self.token_provider.get_token(
            owner=self.owner,
            repo=self.repo,
            actor_id=self.actor_id,
            team_id=self.team_id,
        )
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
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        delay = self.initial_backoff_seconds
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        json=payload if payload is not None else None,
                        params=params if params is not None else None,
                    )
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

    @staticmethod
    def _parse_error_payload(response: httpx.Response) -> tuple[str, list[dict[str, Any]]]:
        message = ""
        parsed_errors: list[dict[str, Any]] = []
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            return message, parsed_errors

        message = str(payload.get("message") or "").strip()
        errors = payload.get("errors")
        if isinstance(errors, dict):
            errors = [errors]
        if isinstance(errors, list):
            for entry in errors:
                if isinstance(entry, dict):
                    parsed_errors.append(entry)
                elif entry is not None:
                    parsed_errors.append({"message": str(entry)})
        return message, parsed_errors

    @staticmethod
    def _pr_error_indicates_existing(message: str, errors: list[dict[str, Any]]) -> bool:
        blob = " ".join(
            [message]
            + [str(item.get("message") or "") for item in errors]
            + [str(item.get("code") or "") for item in errors]
        ).lower()
        if "pull request already exists" in blob:
            return True
        if "already exists for" in blob and "pull request" in blob:
            return True
        return any(str(item.get("code") or "").strip().lower() == "already_exists" for item in errors)

    async def _find_existing_pull_request_url(self, *, head: str, base: str) -> str:
        lookup_head = head if ":" in head else f"{self.owner}:{head}"
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls"
        params = {
            "state": "open",
            "head": lookup_head,
            "base": base,
            "per_page": 1,
        }
        response = await self._request_with_retry(method="GET", url=url, params=params)
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            return ""
        first = payload[0]
        if not isinstance(first, dict):
            return ""
        return str(first.get("html_url") or "").strip()

    def _format_pr_validation_error(
        self,
        *,
        head: str,
        base: str,
        response: httpx.Response,
    ) -> str:
        message, errors = self._parse_error_payload(response)
        details: list[str] = []
        for item in errors:
            tokens: list[str] = []
            for key in ("resource", "field", "code", "message"):
                value = str(item.get(key) or "").strip()
                if value:
                    tokens.append(f"{key}={value}")
            if tokens:
                details.append(", ".join(tokens))
        detail_suffix = f" Details: {' | '.join(details)}" if details else ""
        reason = message or f"HTTP {response.status_code}"
        return (
            f"GitHub rejected PR creation for `{self.owner}/{self.repo}` "
            f"(head=`{head}`, base=`{base}`): {reason}.{detail_suffix}"
        )

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
        try:
            r = await self._request_with_retry(method="POST", url=url, payload=payload)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response is None or response.status_code != 422:
                raise

            message, errors = self._parse_error_payload(response)
            if self._pr_error_indicates_existing(message, errors):
                try:
                    existing_url = await self._find_existing_pull_request_url(head=head, base=base)
                except Exception:  # noqa: BLE001
                    existing_url = ""
                if existing_url:
                    return existing_url

            raise RuntimeError(
                self._format_pr_validation_error(head=head, base=base, response=response)
            ) from exc
        data = r.json()
        return str(data.get("html_url", "")).strip()

    async def update_pull_request_body(self, *, pr_number: int, body: str) -> None:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        payload = {"body": body}
        await self._request_with_retry(method="PATCH", url=url, payload=payload)


def get_github_adapter(
    *,
    owner: str = "",
    repo: str = "",
    strict: bool = False,
    actor_id: str = "",
    team_id: str = "",
) -> GitHubAdapter:
    settings = get_settings()

    if settings.mock_mode or not settings.github_enabled:
        return MockGitHubAdapter()

    resolved_owner = (owner or settings.github_repo_owner or "").strip()
    resolved_repo = (repo or settings.github_repo_name or "").strip()
    if not (resolved_owner and resolved_repo):
        message = "GitHub enabled but missing owner/repo."
        if strict:
            raise RuntimeError(message)
        logger.warning("github_adapter_fallback_to_mock reason=%s", message)
        return MockGitHubAdapter()

    mode = settings.github_auth_mode_normalized()
    user_oauth_required = settings.github_user_oauth_required_effective()
    if not user_oauth_required:
        if mode in {"", "token", "pat"} and not settings.github_token:
            message = "GitHub token auth selected but GITHUB_TOKEN is empty."
            if strict:
                raise RuntimeError(message)
            logger.warning("github_adapter_fallback_to_mock reason=%s", message)
            return MockGitHubAdapter()
        if mode == "app":
            if not settings.github_app_id:
                message = "GitHub App auth selected but GITHUB_APP_ID is missing."
                if strict:
                    raise RuntimeError(message)
                logger.warning("github_adapter_fallback_to_mock reason=%s", message)
                return MockGitHubAdapter()
            if not settings.github_app_private_key and not settings.github_app_private_key_path:
                message = "GitHub App auth selected but private key is missing."
                if strict:
                    raise RuntimeError(message)
                logger.warning("github_adapter_fallback_to_mock reason=%s", message)
                return MockGitHubAdapter()
    if mode not in {"", "token", "pat", "app"}:
        message = f"Unsupported GITHUB_AUTH_MODE={settings.github_auth_mode}."
        if strict:
            raise RuntimeError(message)
        logger.warning("github_adapter_fallback_to_mock reason=%s", message)
        return MockGitHubAdapter()

    return RealGitHubAdapter(
        owner=resolved_owner,
        repo=resolved_repo,
        api_base=settings.github_api_base,
        actor_id=actor_id,
        team_id=team_id,
    )
