from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from app.config import get_settings


@dataclass
class AuthenticatedUser:
    """Identity extracted from edge SSO headers or internal service token."""

    user_id: str
    email: str
    groups: set[str]
    auth_source: str

    @property
    def actor_id(self) -> str:
        return self.user_id or self.email

    def identity_candidates(self) -> set[str]:
        values = {self.user_id.strip(), self.email.strip()}
        lowered = {v.lower() for v in values if v}
        return lowered


def _parse_groups(raw: str) -> set[str]:
    if not raw:
        return set()
    tokens = [x.strip().lower() for x in re.split(r"[;,]", raw) if x.strip()]
    return set(tokens)


def _normalize_identity(value: str) -> str:
    return value.strip().lower()


def _service_token_user(request: Request) -> AuthenticatedUser:
    settings = get_settings()
    actor_header = (settings.auth_service_actor_header or "").strip() or "X-Feature-Factory-Actor"
    actor = request.headers.get(actor_header, "").strip() or "service-token"
    groups = settings.service_auth_group_set()
    return AuthenticatedUser(
        user_id=actor,
        email=actor,
        groups=groups,
        auth_source="api_token",
    )


def require_authenticated_user(request: Request) -> AuthenticatedUser:
    """Resolve authenticated user for protected UI/API routes.

    Modes:
    - disabled: local/dev, grants local test identity.
    - api_token: requires X-FF-Token.
    - edge_sso: requires trusted edge identity headers (or service token).
    """

    settings = get_settings()
    mode = settings.auth_mode_normalized()
    required_token = (settings.api_auth_token or "").strip()
    supplied_token = (request.headers.get("X-FF-Token") or "").strip()
    token_valid = bool(required_token) and supplied_token == required_token

    # Service token is accepted in all modes when configured.
    if token_valid:
        return _service_token_user(request)

    if mode in {"", "disabled", "none"}:
        # Local default: keep developer workflow simple.
        return AuthenticatedUser(
            user_id="local-user",
            email="local-user",
            groups={"engineering", "admins"},
            auth_source="disabled",
        )

    if mode == "api_token":
        raise HTTPException(status_code=401, detail="Missing or invalid X-FF-Token")

    if mode != "edge_sso":
        raise HTTPException(status_code=500, detail=f"Unsupported AUTH_MODE '{settings.auth_mode}'")

    email_header = (settings.auth_header_email or "").strip() or "X-Forwarded-Email"
    groups_header = (settings.auth_header_groups or "").strip() or "X-Forwarded-Groups"

    email = (request.headers.get(email_header) or "").strip()
    if not email:
        raise HTTPException(status_code=401, detail=f"Missing trusted identity header: {email_header}")

    groups = _parse_groups(request.headers.get(groups_header, ""))
    return AuthenticatedUser(
        user_id=email,
        email=email,
        groups=groups,
        auth_source="edge_sso",
    )


def _rule_matches_user(rule: str, user: AuthenticatedUser) -> bool:
    candidate = _normalize_identity(rule)
    if not candidate:
        return False
    if candidate == "any_authenticated":
        return True
    if candidate.startswith("group:"):
        wanted = candidate.removeprefix("group:").strip()
        return bool(wanted) and wanted in user.groups
    if candidate.startswith("user:"):
        wanted = candidate.removeprefix("user:").strip()
        return bool(wanted) and wanted in user.identity_candidates()
    return candidate in user.identity_candidates()


def _enforce_rules(*, user: AuthenticatedUser, rules: list[str], action: str) -> None:
    if any(_rule_matches_user(rule, user) for rule in rules):
        return
    raise HTTPException(status_code=403, detail=f"User '{user.actor_id}' is not allowed to {action}")


def _in_reviewer_allowlist(user: AuthenticatedUser) -> bool:
    settings = get_settings()
    allowlist = settings.reviewer_allowed_user_set()
    if not allowlist:
        return False
    normalized_allowlist = {entry.strip().lower() for entry in allowlist if entry.strip()}
    return bool(user.identity_candidates().intersection(normalized_allowlist))


def require_can_request_or_update_spec(user: AuthenticatedUser = Depends(require_authenticated_user)) -> AuthenticatedUser:
    settings = get_settings()
    _enforce_rules(user=user, rules=settings.rbac_requester_rules(), action="create or update specs")
    return user


def require_can_build(user: AuthenticatedUser = Depends(require_authenticated_user)) -> AuthenticatedUser:
    settings = get_settings()
    _enforce_rules(user=user, rules=settings.rbac_builder_rules(), action="run builds")
    return user


def require_can_approve(user: AuthenticatedUser = Depends(require_authenticated_user)) -> AuthenticatedUser:
    settings = get_settings()
    if _in_reviewer_allowlist(user):
        return user
    _enforce_rules(user=user, rules=settings.rbac_approver_rules(), action="approve features")
    return user


def require_api_auth(_user: AuthenticatedUser = Depends(require_authenticated_user)) -> None:
    """Backward-compatible alias used by older routes/scripts."""
    return None


def _matches_any_rule(*, user: AuthenticatedUser, rules: list[str]) -> bool:
    return any(_rule_matches_user(rule, user) for rule in rules)


def user_can_view_all_features(user: AuthenticatedUser) -> bool:
    settings = get_settings()
    if user.auth_source == "api_token":
        return True
    if _in_reviewer_allowlist(user):
        return True
    return _matches_any_rule(user=user, rules=settings.rbac_builder_rules()) or _matches_any_rule(
        user=user, rules=settings.rbac_approver_rules()
    )


def user_can_access_feature(user: AuthenticatedUser, requester_user_id: str) -> bool:
    if user_can_view_all_features(user):
        return True
    requester = _normalize_identity(requester_user_id)
    if not requester:
        return False
    return requester in user.identity_candidates()
