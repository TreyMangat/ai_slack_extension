from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings


class RepoIndexerError(RuntimeError):
    """Base error for Repo Indexer integration failures."""


class RepoIndexerNotConfiguredError(RepoIndexerError):
    """Raised when INDEXER_BASE_URL is not configured."""


class RepoIndexerRequestError(RepoIndexerError):
    """Raised when Repo Indexer returns an error or is unreachable."""


@dataclass(frozen=True)
class RepoIndexerClient:
    base_url: str
    auth_token: str = ""
    timeout_seconds: float = 4.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RepoIndexerClient":
        base_url = settings.indexer_base_url_normalized()
        if not base_url:
            raise RepoIndexerNotConfiguredError("INDEXER_BASE_URL is not configured")
        timeout_seconds = max(float(settings.indexer_timeout_seconds), 0.5)
        return cls(
            base_url=base_url.rstrip("/"),
            auth_token=(settings.indexer_auth_token or "").strip(),
            timeout_seconds=timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = (self.auth_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-FF-Token"] = token
        return headers

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        fallback_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        targets = [path, *fallback_paths]
        last_error: Exception | None = None
        for idx, target in enumerate(targets):
            url = f"{self.base_url}{target}"
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, json=payload, headers=self._headers())
                if response.status_code == 404 and idx < len(targets) - 1:
                    continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise RepoIndexerRequestError(f"Indexer returned non-object JSON from {target}")
                return data
            except httpx.HTTPStatusError as exc:
                detail = ""
                try:
                    body = exc.response.json()
                    if isinstance(body, dict):
                        detail = str(body.get("detail") or body.get("message") or "").strip()
                except Exception:
                    detail = ""
                message = (
                    f"Indexer request failed ({exc.response.status_code}) at {target}"
                    f"{f': {detail}' if detail else ''}"
                )
                last_error = RepoIndexerRequestError(message)
                if exc.response.status_code == 404 and idx < len(targets) - 1:
                    continue
                raise last_error from exc
            except Exception as exc:  # noqa: BLE001
                last_error = RepoIndexerRequestError(f"Indexer request failed at {target}: {exc}")
                if idx < len(targets) - 1:
                    continue
                raise last_error from exc

        if last_error is None:
            raise RepoIndexerRequestError("Indexer request failed")
        raise last_error

    def search(
        self,
        *,
        query: str,
        top_k_repos: int = 5,
        top_k_chunks: int = 3,
    ) -> dict[str, Any]:
        payload = {
            "query": str(query or "").strip(),
            "top_k_repos": max(int(top_k_repos), 1),
            "top_k_chunks": max(int(top_k_chunks), 1),
        }
        return self._post("/api/indexer/search", payload, fallback_paths=("/search",))

    def suggest_repos_and_branches(
        self,
        *,
        actor_id: str,
        query: str = "",
        top_k_repos: int = 10,
        top_k_branches_per_repo: int = 8,
    ) -> dict[str, Any]:
        payload = {
            "actor_id": str(actor_id or "").strip(),
            "query": str(query or "").strip(),
            "top_k_repos": max(int(top_k_repos), 1),
            "top_k_branches_per_repo": max(int(top_k_branches_per_repo), 1),
        }
        return self._post("/api/indexer/catalog/suggest", payload)


def get_repo_indexer_client(settings: Settings | None = None) -> RepoIndexerClient | None:
    resolved = settings or get_settings()
    try:
        return RepoIndexerClient.from_settings(resolved)
    except RepoIndexerNotConfiguredError:
        return None
