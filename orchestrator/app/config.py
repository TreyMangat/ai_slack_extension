from __future__ import annotations

from functools import lru_cache

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
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    # Internal URL used by the Slack bot service to reach the API inside docker-compose
    orchestrator_internal_url: str = Field(default="http://api:8000", alias="ORCHESTRATOR_INTERNAL_URL")


    # DB / Queue
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(..., alias="REDIS_URL")

    # Security
    secret_key: str = Field(..., alias="SECRET_KEY")

    # Slack
    enable_slack_bot: bool = Field(default=False, alias="ENABLE_SLACK_BOT")
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
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_repo_owner: str = Field(default="", alias="GITHUB_REPO_OWNER")
    github_repo_name: str = Field(default="", alias="GITHUB_REPO_NAME")
    github_api_base: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE")
    github_default_branch: str = Field(default="main", alias="GITHUB_DEFAULT_BRANCH")

    opencode_trigger_comment: str = Field(
        default="/oc Implement this issue. Follow the acceptance criteria. Add tests.",
        alias="OPENCODE_TRIGGER_COMMENT",
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

    def slack_allowed_channel_set(self) -> set[str]:
        return {c.strip() for c in self.slack_allowed_channels.split(",") if c.strip()}

    def slack_allowed_user_set(self) -> set[str]:
        return {u.strip() for u in self.slack_allowed_users.split(",") if u.strip()}

    def reviewer_allowed_user_set(self) -> set[str]:
        return {u.strip() for u in self.reviewer_allowed_users.split(",") if u.strip()}

    def workspace_copy_ignore_patterns(self) -> list[str]:
        return [x.strip() for x in self.workspace_copy_ignore.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
