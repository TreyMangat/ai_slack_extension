from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest


BASE_URL = os.environ.get("PRFACTORY_E2E_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="Set PRFACTORY_E2E_URL=http://localhost:8000 to run E2E tests",
)


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_token = os.environ.get("PRFACTORY_E2E_API_TOKEN", "").strip()
    if api_token:
        headers["X-FF-Token"] = api_token

    auth_email = os.environ.get("PRFACTORY_E2E_AUTH_EMAIL", "").strip()
    if auth_email:
        email_header = os.environ.get("PRFACTORY_E2E_AUTH_HEADER_EMAIL", "X-Forwarded-Email").strip()
        groups_header = os.environ.get("PRFACTORY_E2E_AUTH_HEADER_GROUPS", "X-Forwarded-Groups").strip()
        auth_groups = os.environ.get("PRFACTORY_E2E_AUTH_GROUPS", "engineering,admins").strip()
        headers[email_header] = auth_email
        headers[groups_header] = auth_groups

    return headers


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=15.0)


def _feature_payload(*, title_prefix: str = "Local pytest E2E") -> dict[str, object]:
    unique = uuid.uuid4().hex[:8]
    return {
        "spec": {
            "title": f"{title_prefix} {unique}",
            "problem": "Add a dark mode toggle to the settings page.",
            "business_justification": "Users need a low-light experience during extended sessions.",
            "implementation_mode": "new_feature",
            "source_repos": [],
            "proposed_solution": "Add a persisted theme toggle in settings and apply it app-wide.",
            "acceptance_criteria": [
                "A dark mode toggle appears in settings",
                "The theme choice persists across reloads",
            ],
            "non_goals": ["No full design refresh"],
            "repo": "",
            "base_branch": "",
            "risk_flags": [],
            "links": [],
        },
        "requester_user_id": "pytest-e2e",
    }


def _create_feature(client: httpx.Client, *, title_prefix: str = "Local pytest E2E") -> dict[str, object]:
    response = client.post("/api/feature-requests", json=_feature_payload(title_prefix=title_prefix))
    response.raise_for_status()
    return response.json()


def _poll_feature(client: httpx.Client, feature_id: str, *, timeout_seconds: float = 45.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    latest: dict[str, object] = {}

    while time.time() < deadline:
        response = client.get(f"/api/feature-requests/{feature_id}")
        response.raise_for_status()
        latest = response.json()
        if latest.get("status") not in {"BUILDING", "READY_FOR_BUILD"}:
            return latest
        time.sleep(2)

    return latest


def test_health_endpoint_returns_ok() -> None:
    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_runtime_has_openrouter_block() -> None:
    with _client() as client:
        response = client.get("/health/runtime")

    response.raise_for_status()
    payload = response.json()
    assert "openrouter" in payload
    assert {"configured", "mini_model", "frontier_model"} <= set(payload["openrouter"].keys())


def test_create_and_revalidate_feature() -> None:
    with _client() as client:
        created = _create_feature(client, title_prefix="Create revalidate")
        feature_id = created["id"]

        detail_response = client.get(f"/api/feature-requests/{feature_id}")
        detail_response.raise_for_status()
        detail = detail_response.json()

        revalidate_response = client.post(f"/api/feature-requests/{feature_id}/revalidate")
        revalidate_response.raise_for_status()
        revalidated = revalidate_response.json()

    assert detail["id"] == feature_id
    assert detail["status"] == "READY_FOR_BUILD"
    assert revalidated["id"] == feature_id
    assert revalidated["status"] == "READY_FOR_BUILD"
    assert "llm_spec_analysis" in revalidated


def test_full_lifecycle_mock_mode() -> None:
    with _client() as client:
        runtime_response = client.get("/health/runtime")
        runtime_response.raise_for_status()
        runtime = runtime_response.json()
        if not bool(runtime.get("runtime", {}).get("mock_mode")):
            pytest.skip("Local E2E lifecycle test is intended for mock mode")

        created = _create_feature(client, title_prefix="Full lifecycle")
        feature_id = created["id"]

        revalidate_response = client.post(f"/api/feature-requests/{feature_id}/revalidate")
        revalidate_response.raise_for_status()

        build_response = client.post(f"/api/feature-requests/{feature_id}/build")
        build_response.raise_for_status()
        build_payload = build_response.json()

        final_feature = _poll_feature(client, feature_id)

    assert build_payload["ok"] is True
    assert final_feature["status"] == "PREVIEW_READY"
    assert isinstance(final_feature.get("events"), list)


def test_invalid_transition_rejected() -> None:
    with _client() as client:
        created = _create_feature(client, title_prefix="Approve too early")
        feature_id = created["id"]
        response = client.post(f"/api/feature-requests/{feature_id}/approve")

    assert response.status_code == 400


def test_duplicate_build_rejected() -> None:
    with _client() as client:
        created = _create_feature(client, title_prefix="Duplicate build")
        feature_id = created["id"]

        first_build = client.post(f"/api/feature-requests/{feature_id}/build")
        first_build.raise_for_status()

        second_build = client.post(f"/api/feature-requests/{feature_id}/build")

    assert second_build.status_code in {200, 400, 409}
    if second_build.status_code == 200:
        payload = second_build.json()
        assert payload["ok"] is True
        assert payload["already_in_progress"] is True
