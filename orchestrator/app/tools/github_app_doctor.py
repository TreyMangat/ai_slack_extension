from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from app.config import get_settings
from app.services.github_auth import GitHubAuthError, GitHubTokenProvider


_PERM_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}
_REQUIRED_PERMISSIONS = {
    "metadata": "read",
    "issues": "write",
}
_CLONE_REQUIRED_PERMISSION = ("contents", "read")
_FUTURE_RECOMMENDED_PERMISSION = ("pull_requests", "write")
_NATIVE_LLM_REQUIRED_PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
}


def _normalize_permission(value: Any) -> str:
    text = str(value or "none").strip().lower()
    if text not in _PERM_LEVELS:
        return "none"
    return text


def _has_at_least(actual: str, needed: str) -> bool:
    return _PERM_LEVELS[_normalize_permission(actual)] >= _PERM_LEVELS[_normalize_permission(needed)]


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return response.text.strip() or f"http {response.status_code}"
    if isinstance(payload, dict):
        message = str(payload.get("message", "")).strip()
        if message:
            return message
    return json.dumps(payload)


def _request_json(client: httpx.Client, method: str, url: str, headers: dict[str, str]) -> tuple[dict[str, Any], str]:
    response = client.request(method, url, headers=headers)
    if response.status_code >= 400:
        return {}, f"{response.status_code} {_extract_error_message(response)}"
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return {}, f"invalid JSON response from {url}"
    if not isinstance(data, dict):
        return {}, f"unexpected JSON type from {url}"
    return data, ""


def main() -> int:
    settings = get_settings()
    provider = GitHubTokenProvider(settings)

    errors: list[str] = []
    warnings: list[str] = []

    if not settings.github_enabled:
        warnings.append("GITHUB_ENABLED=false (the app integration is disabled in runtime config)")

    mode = settings.github_auth_mode_normalized()
    if mode != "app":
        errors.append(f"GITHUB_AUTH_MODE must be 'app' (found '{settings.github_auth_mode}')")

    if not (settings.github_repo_owner and settings.github_repo_name):
        errors.append("GITHUB_REPO_OWNER and GITHUB_REPO_NAME are required")

    try:
        app_jwt = provider._mint_app_jwt()  # noqa: SLF001 - doctor utility
    except GitHubAuthError as e:
        errors.append(str(e))
        app_jwt = ""

    installation_id = (settings.github_app_installation_id or "").strip()
    if not installation_id:
        errors.append("GITHUB_APP_INSTALLATION_ID is required")

    api_base = settings.github_api_base.rstrip("/")
    app_name = ""
    install_account = ""
    repo_selection = ""
    permissions: dict[str, Any] = {}
    permissions_loaded = False
    repo_access_ok = False
    repo_url = f"{settings.github_repo_owner}/{settings.github_repo_name}"

    if app_jwt and installation_id:
        app_headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=30) as client:
            app_meta, app_meta_err = _request_json(client, "GET", f"{api_base}/app", app_headers)
            if app_meta_err:
                errors.append(f"failed to query /app: {app_meta_err}")
            else:
                app_name = str(app_meta.get("name", "")).strip()

            install_meta, install_meta_err = _request_json(
                client,
                "GET",
                f"{api_base}/app/installations/{installation_id}",
                app_headers,
            )
            if install_meta_err:
                errors.append(f"failed to query installation {installation_id}: {install_meta_err}")
            else:
                permissions = dict(install_meta.get("permissions") or {})
                permissions_loaded = True
                account = install_meta.get("account") or {}
                if isinstance(account, dict):
                    install_account = str(account.get("login", "")).strip()
                repo_selection = str(install_meta.get("repository_selection", "")).strip()

            if settings.github_repo_owner and settings.github_repo_name:
                repo_install, repo_install_err = _request_json(
                    client,
                    "GET",
                    f"{api_base}/repos/{settings.github_repo_owner}/{settings.github_repo_name}/installation",
                    app_headers,
                )
                if repo_install_err:
                    errors.append(
                        f"app is not installed on repo {repo_url} or repo is inaccessible: {repo_install_err}"
                    )
                else:
                    repo_access_ok = True
                    repo_install_id = str(repo_install.get("id", "")).strip()
                    if repo_install_id and repo_install_id != installation_id:
                        warnings.append(
                            f"repo installation id is {repo_install_id}, "
                            f"but GITHUB_APP_INSTALLATION_ID is {installation_id}"
                        )

    try:
        installation_token = provider.get_token()
    except GitHubAuthError as e:
        errors.append(f"failed to mint installation token: {e}")
        installation_token = ""

    if installation_token and settings.github_repo_owner and settings.github_repo_name:
        token_headers = {
            "Authorization": f"Bearer {installation_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=30) as client:
            _, repo_check_err = _request_json(
                client,
                "GET",
                f"{api_base}/repos/{settings.github_repo_owner}/{settings.github_repo_name}",
                token_headers,
            )
            if repo_check_err:
                errors.append(f"installation token cannot access repo {repo_url}: {repo_check_err}")

    clone_perm_name, clone_perm_level = _CLONE_REQUIRED_PERMISSION
    future_perm_name, future_perm_level = _FUTURE_RECOMMENDED_PERMISSION

    if permissions_loaded:
        for permission_name, minimum_level in _REQUIRED_PERMISSIONS.items():
            actual = _normalize_permission(permissions.get(permission_name))
            if not _has_at_least(actual, minimum_level):
                errors.append(
                    f"missing permission '{permission_name}': have '{actual}', need at least '{minimum_level}'"
                )

        clone_actual = _normalize_permission(permissions.get(clone_perm_name))
        if settings.workspace_enable_git_clone:
            if not _has_at_least(clone_actual, clone_perm_level):
                errors.append(
                    f"WORKSPACE_ENABLE_GIT_CLONE=true requires '{clone_perm_name}:{clone_perm_level}' "
                    f"(current '{clone_actual}')"
                )
        elif not _has_at_least(clone_actual, clone_perm_level):
            warnings.append(
                f"'{clone_perm_name}:{clone_perm_level}' is recommended only if you plan to enable "
                "WORKSPACE_ENABLE_GIT_CLONE=true"
            )

        future_actual = _normalize_permission(permissions.get(future_perm_name))
        if settings.coderunner_mode_normalized() == "native_llm":
            for permission_name, minimum_level in _NATIVE_LLM_REQUIRED_PERMISSIONS.items():
                actual = _normalize_permission(permissions.get(permission_name))
                if not _has_at_least(actual, minimum_level):
                    errors.append(
                        f"CODERUNNER_MODE=native_llm requires '{permission_name}:{minimum_level}' "
                        f"(current '{actual}')"
                    )
        elif not _has_at_least(future_actual, future_perm_level):
            warnings.append(
                f"'{future_perm_name}:{future_perm_level}' is optional for future PR automation "
                "(not required for current issue/comment flow)"
            )
    else:
        warnings.append("installation permissions could not be loaded; fix prior errors and run again")

    print("GitHub App doctor")
    print(f"  github_enabled: {settings.github_enabled}")
    print(f"  github_auth_mode: {settings.github_auth_mode}")
    print(f"  github_api_base: {api_base}")
    print(f"  app_name: {app_name or '(unknown)'}")
    print(f"  app_id: {settings.github_app_id or '(missing)'}")
    print(f"  installation_id: {installation_id or '(missing)'}")
    print(f"  installation_account: {install_account or '(unknown)'}")
    print(f"  repository_selection: {repo_selection or '(unknown)'}")
    print(f"  target_repo: {repo_url if settings.github_repo_owner and settings.github_repo_name else '(missing)'}")
    print(f"  repo_access_via_app: {repo_access_ok}")
    print("  permissions:")
    for name in sorted(set(list(_REQUIRED_PERMISSIONS.keys()) + [clone_perm_name, future_perm_name])):
        print(f"    - {name}: {_normalize_permission(permissions.get(name))}")

    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"  - {item}")

    if errors:
        print("Result: FAIL")
        print("Errors:")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("Result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
