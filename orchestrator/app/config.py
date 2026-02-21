from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration.

    This is intentionally environment-variable driven so it works well in:
    - local docker compose
    - future Kubernetes/Cloud deployments
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    mock_mode: bool = Field(default=True, alias="MOCK_MODE")
    enforce_production_security: bool = Field(default=True, alias="ENFORCE_PRODUCTION_SECURITY")
    enable_api_docs: bool = Field(default=True, alias="ENABLE_API_DOCS")
    enable_api_docs_in_prod: bool = Field(default=False, alias="ENABLE_API_DOCS_IN_PROD")
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    # Internal URL used by the Slack bot service to reach the API inside docker-compose
    orchestrator_internal_url: str = Field(default="http://api:8000", alias="ORCHESTRATOR_INTERNAL_URL")
    run_migrations: bool = Field(default=False, alias="RUN_MIGRATIONS")
    migration_bootstrap_stamp: bool = Field(default=False, alias="MIGRATION_BOOTSTRAP_STAMP")


    # DB / Queue
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")

    # Security
    secret_key: str = Field(..., alias="SECRET_KEY")
    auth_mode: str = Field(default="disabled", alias="AUTH_MODE")
    sso_provider: str = Field(default="", alias="SSO_PROVIDER")
    idp: str = Field(default="", alias="IDP")
    hosting_target: str = Field(default="", alias="HOSTING_TARGET")
    auth_header_email: str = Field(default="X-Forwarded-Email", alias="AUTH_HEADER_EMAIL")
    auth_header_groups: str = Field(default="X-Forwarded-Groups", alias="AUTH_HEADER_GROUPS")
    auth_service_actor_header: str = Field(default="X-Feature-Factory-Actor", alias="AUTH_SERVICE_ACTOR_HEADER")
    service_auth_groups: str = Field(default="engineering,admins", alias="SERVICE_AUTH_GROUPS")
    rbac_requesters: str = Field(default="any_authenticated", alias="RBAC_REQUESTERS")
    rbac_builders: str = Field(default="group:engineering", alias="RBAC_BUILDERS")
    rbac_approvers: str = Field(default="group:admins", alias="RBAC_APPROVERS")

    # Slack
    enable_slack_bot: bool = Field(default=False, alias="ENABLE_SLACK_BOT")
    slack_mode: str = Field(default="socket", alias="SLACK_MODE")
    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_app_token: str = Field(default="", alias="SLACK_APP_TOKEN")
    slack_signing_secret: str = Field(default="", alias="SLACK_SIGNING_SECRET")
    slack_default_channel: str = Field(default="", alias="SLACK_DEFAULT_CHANNEL")

    slack_allowed_channels: str = Field(default="", alias="SLACK_ALLOWED_CHANNELS")
    slack_allowed_users: str = Field(default="", alias="SLACK_ALLOWED_USERS")
    slack_require_mention: bool = Field(default=True, alias="SLACK_REQUIRE_MENTION")
    reviewer_allowed_users: str = Field(default="", alias="REVIEWER_ALLOWED_USERS")
    reviewer_channel_id: str = Field(default="", alias="REVIEWER_CHANNEL_ID")

    # GitHub
    github_enabled: bool = Field(default=False, alias="GITHUB_ENABLED")
    github_auth_mode: str = Field(default="token", alias="GITHUB_AUTH_MODE")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_app_id: str = Field(default="", alias="GITHUB_APP_ID")
    github_app_installation_id: str = Field(default="", alias="GITHUB_APP_INSTALLATION_ID")
    github_app_private_key: str = Field(default="", alias="GITHUB_APP_PRIVATE_KEY")
    github_app_private_key_path: str = Field(default="", alias="GITHUB_APP_PRIVATE_KEY_PATH")
    github_app_jwt_ttl_seconds: int = Field(default=540, alias="GITHUB_APP_JWT_TTL_SECONDS")
    github_repo_owner: str = Field(default="", alias="GITHUB_REPO_OWNER")
    github_repo_name: str = Field(default="", alias="GITHUB_REPO_NAME")
    github_api_base: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE")
    github_default_branch: str = Field(default="main", alias="GITHUB_DEFAULT_BRANCH")

    opencode_trigger_comment: str = Field(
        default="/oc Implement this issue. Follow the acceptance criteria. Add tests.",
        alias="OPENCODE_TRIGGER_COMMENT",
    )

    # Code runner strategy:
    # - opencode: trigger external OpenCode workflow via issue comment (default)
    # - native_llm: run in-container LLM coding loop (experimental)
    coderunner_mode: str = Field(default="opencode", alias="CODERUNNER_MODE")

    # Native LLM runner settings (used when CODERUNNER_MODE=native_llm and MOCK_MODE=false)
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_api_base: str = Field(default="https://api.openai.com/v1", alias="LLM_API_BASE")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4.1-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    llm_max_patch_rounds: int = Field(default=3, alias="LLM_MAX_PATCH_ROUNDS")
    llm_repo_max_files: int = Field(default=250, alias="LLM_REPO_MAX_FILES")
    llm_repo_file_max_chars: int = Field(default=5000, alias="LLM_REPO_FILE_MAX_CHARS")
    llm_install_command: str = Field(default="", alias="LLM_INSTALL_COMMAND")
    llm_test_command: str = Field(default="pytest -q", alias="LLM_TEST_COMMAND")
    llm_push_branch_prefix: str = Field(default="feature-factory", alias="LLM_PUSH_BRANCH_PREFIX")
    llm_commit_author_name: str = Field(default="feature-factory-bot", alias="LLM_COMMIT_AUTHOR_NAME")
    llm_commit_author_email: str = Field(
        default="feature-factory-bot@local.invalid",
        alias="LLM_COMMIT_AUTHOR_EMAIL",
    )

    # Policy
    disable_automerge: bool = Field(default=True, alias="DISABLE_AUTOMERGE")
    api_auth_token: str = Field(default="", alias="API_AUTH_TOKEN")

    # Integration callbacks (e.g., CI/OpenCode posting build results back)
    integration_webhook_secret: str = Field(default="", alias="INTEGRATION_WEBHOOK_SECRET")
    integration_webhook_ttl_seconds: int = Field(default=300, alias="INTEGRATION_WEBHOOK_TTL_SECONDS")

    # Workspace isolation / repo snapshot settings
    workspace_root: str = Field(default="/tmp/feature_factory_workspaces", alias="WORKSPACE_ROOT")
    workspace_enable_git_clone: bool = Field(default=False, alias="WORKSPACE_ENABLE_GIT_CLONE")
    workspace_max_source_repos: int = Field(default=5, alias="WORKSPACE_MAX_SOURCE_REPOS")
    workspace_git_clone_timeout_seconds: int = Field(default=120, alias="WORKSPACE_GIT_CLONE_TIMEOUT_SECONDS")
    workspace_local_copy_root: str = Field(default="/app", alias="WORKSPACE_LOCAL_COPY_ROOT")
    workspace_copy_ignore: str = Field(
        default=".git,.venv,node_modules,__pycache__,.pytest_cache,.mypy_cache",
        alias="WORKSPACE_COPY_IGNORE",
    )
    workspace_retention_hours: int = Field(default=24, alias="WORKSPACE_RETENTION_HOURS")
    workspace_cleanup_max_per_run: int = Field(default=50, alias="WORKSPACE_CLEANUP_MAX_PER_RUN")
    workspace_cleanup_interval_minutes: int = Field(default=60, alias="WORKSPACE_CLEANUP_INTERVAL_MINUTES")
    workspace_retention_hours_with_pr: int = Field(default=168, alias="WORKSPACE_RETENTION_HOURS_WITH_PR")
    workspace_retention_hours_without_pr: int = Field(default=24, alias="WORKSPACE_RETENTION_HOURS_WITHOUT_PR")
    workspace_retention_hours_failed: int = Field(default=12, alias="WORKSPACE_RETENTION_HOURS_FAILED")
    callback_stale_alert_minutes: int = Field(default=30, alias="CALLBACK_STALE_ALERT_MINUTES")
    callback_stale_alert_cooldown_minutes: int = Field(default=60, alias="CALLBACK_STALE_ALERT_COOLDOWN_MINUTES")
    callback_stale_check_max_per_run: int = Field(default=50, alias="CALLBACK_STALE_CHECK_MAX_PER_RUN")

    def is_production(self) -> bool:
        return (self.app_env or "").strip().lower() in {"prod", "production"}

    def docs_enabled(self) -> bool:
        if self.is_production():
            return self.enable_api_docs_in_prod
        return self.enable_api_docs

    def validate_runtime_guardrails(self) -> None:
        if not self.enforce_production_security or not self.is_production():
            return

        failures: list[str] = []
        mode = self.auth_mode_normalized()
        if mode in {"", "disabled", "none"}:
            failures.append("AUTH_MODE must not be disabled in production")
        if self.mock_mode:
            failures.append("MOCK_MODE must be false in production")
        if not (self.api_auth_token or "").strip():
            failures.append("API_AUTH_TOKEN must be configured in production")
        if not (self.integration_webhook_secret or "").strip():
            failures.append("INTEGRATION_WEBHOOK_SECRET must be configured in production")
        if (self.secret_key or "").strip() in {"", "dev-change-me", "change-me"}:
            failures.append("SECRET_KEY must be changed from insecure defaults in production")
        if not self.disable_automerge:
            failures.append("DISABLE_AUTOMERGE must stay true in production")
        if self.coderunner_mode_normalized() == "native_llm" and not (self.llm_api_key or "").strip():
            failures.append("LLM_API_KEY must be configured when CODERUNNER_MODE=native_llm in production")

        if failures:
            joined = "; ".join(failures)
            raise RuntimeError(f"Production security guardrail check failed: {joined}")

    def validate_startup_prerequisites(self) -> None:
        failures: list[str] = []
        if self.github_enabled and self.github_auth_mode_normalized() == "app":
            key_path = (self.github_app_private_key_path or "").strip()
            inline_key = (self.github_app_private_key or "").strip()
            if key_path:
                if not Path(key_path).exists():
                    failures.append(f"GITHUB_APP_PRIVATE_KEY_PATH not found in runtime container: {key_path}")
            elif not inline_key:
                failures.append("GitHub App auth requires GITHUB_APP_PRIVATE_KEY_PATH or GITHUB_APP_PRIVATE_KEY")
        if failures:
            raise RuntimeError("; ".join(failures))

    def runtime_diagnostics(self) -> dict[str, object]:
        key_path = (self.github_app_private_key_path or "").strip()
        key_file_exists = bool(key_path) and Path(key_path).exists()
        return {
            "app_env": (self.app_env or "").strip().lower() or "local",
            "auth_mode": self.auth_mode_normalized() or "disabled",
            "mock_mode": bool(self.mock_mode),
            "coderunner_mode": self.coderunner_mode_normalized() or "opencode",
            "docs_enabled": bool(self.docs_enabled()),
            "enable_slack_bot": bool(self.enable_slack_bot),
            "github_enabled": bool(self.github_enabled),
            "github_auth_mode": self.github_auth_mode_normalized() or "token",
            "github_app_private_key_configured": bool(key_path or (self.github_app_private_key or "").strip()),
            "github_app_private_key_file_exists": bool(key_file_exists),
            "workspace_enable_git_clone": bool(self.workspace_enable_git_clone),
            "integration_webhook_configured": bool((self.integration_webhook_secret or "").strip()),
        }

    def slack_allowed_channel_set(self) -> set[str]:
        return {c.strip() for c in self.slack_allowed_channels.split(",") if c.strip()}

    def slack_allowed_user_set(self) -> set[str]:
        return {u.strip() for u in self.slack_allowed_users.split(",") if u.strip()}

    def reviewer_allowed_user_set(self) -> set[str]:
        return {u.strip() for u in self.reviewer_allowed_users.split(",") if u.strip()}

    def workspace_copy_ignore_patterns(self) -> list[str]:
        return [x.strip() for x in self.workspace_copy_ignore.split(",") if x.strip()]

    @staticmethod
    def _parse_csv(value: str) -> list[str]:
        return [x.strip() for x in value.split(",") if x.strip()]

    def auth_mode_normalized(self) -> str:
        return (self.auth_mode or "").strip().lower()

    def github_auth_mode_normalized(self) -> str:
        return (self.github_auth_mode or "").strip().lower()

    def coderunner_mode_normalized(self) -> str:
        return (self.coderunner_mode or "").strip().lower()

    def service_auth_group_set(self) -> set[str]:
        return {g.lower() for g in self._parse_csv(self.service_auth_groups)}

    def rbac_requester_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_requesters)

    def rbac_builder_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_builders)

    def rbac_approver_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_approvers)


@lru_cache
def get_settings() -> Settings:
    return Settings()
