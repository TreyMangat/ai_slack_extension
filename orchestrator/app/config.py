from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlsplit

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
    app_display_name: str = Field(default="PRFactory", alias="APP_DISPLAY_NAME")
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
    enable_slack_oauth: bool = Field(default=False, alias="ENABLE_SLACK_OAUTH")
    slack_client_id: str = Field(default="", alias="SLACK_CLIENT_ID")
    slack_client_secret: str = Field(default="", alias="SLACK_CLIENT_SECRET")
    slack_oauth_scopes: str = Field(
        default=(
            "chat:write,commands,channels:read,channels:history,channels:join,"
            "groups:read,groups:history,im:read,im:history,mpim:read,mpim:history"
        ),
        alias="SLACK_OAUTH_SCOPES",
    )
    slack_oauth_user_scopes: str = Field(default="", alias="SLACK_OAUTH_USER_SCOPES")
    slack_oauth_install_path: str = Field(default="/api/slack/install", alias="SLACK_OAUTH_INSTALL_PATH")
    slack_oauth_callback_path: str = Field(default="/api/slack/oauth/callback", alias="SLACK_OAUTH_CALLBACK_PATH")
    slack_oauth_redirect_uri: str = Field(default="", alias="SLACK_OAUTH_REDIRECT_URI")
    slack_oauth_state_expiration_seconds: int = Field(default=600, alias="SLACK_OAUTH_STATE_EXPIRATION_SECONDS")
    slack_app_id: str = Field(default="", alias="SLACK_APP_ID")
    slack_team_id: str = Field(default="", alias="SLACK_TEAM_ID")
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
    github_app_slug: str = Field(default="", alias="GITHUB_APP_SLUG")
    github_app_install_url: str = Field(default="", alias="GITHUB_APP_INSTALL_URL")
    github_repo_owner: str = Field(default="", alias="GITHUB_REPO_OWNER")
    github_repo_name: str = Field(default="", alias="GITHUB_REPO_NAME")
    github_api_base: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE")
    github_default_branch: str = Field(default="main", alias="GITHUB_DEFAULT_BRANCH")
    enable_github_user_oauth: bool = Field(default=False, alias="ENABLE_GITHUB_USER_OAUTH")
    github_oauth_client_id: str = Field(default="", alias="GITHUB_OAUTH_CLIENT_ID")
    github_oauth_client_secret: str = Field(default="", alias="GITHUB_OAUTH_CLIENT_SECRET")
    github_oauth_scopes: str = Field(default="repo,read:user", alias="GITHUB_OAUTH_SCOPES")
    github_oauth_install_path: str = Field(default="/api/github/install", alias="GITHUB_OAUTH_INSTALL_PATH")
    github_oauth_callback_path: str = Field(default="/api/github/oauth/callback", alias="GITHUB_OAUTH_CALLBACK_PATH")
    github_oauth_redirect_uri: str = Field(default="", alias="GITHUB_OAUTH_REDIRECT_URI")
    github_oauth_state_expiration_seconds: int = Field(default=600, alias="GITHUB_OAUTH_STATE_EXPIRATION_SECONDS")
    github_user_oauth_required: bool = Field(default=True, alias="GITHUB_USER_OAUTH_REQUIRED")
    github_user_token_encryption_key: str = Field(default="", alias="GITHUB_USER_TOKEN_ENCRYPTION_KEY")

    # Repo Indexer integration (external Repo_Indexer service)
    indexer_base_url: str = Field(default="", alias="INDEXER_BASE_URL")
    indexer_auth_token: str = Field(default="", alias="INDEXER_AUTH_TOKEN")
    indexer_timeout_seconds: float = Field(default=4.0, alias="INDEXER_TIMEOUT_SECONDS")
    indexer_top_k_repos: int = Field(default=5, alias="INDEXER_TOP_K_REPOS")
    indexer_top_k_chunks: int = Field(default=3, alias="INDEXER_TOP_K_CHUNKS")
    indexer_top_k_branches_per_repo: int = Field(default=8, alias="INDEXER_TOP_K_BRANCHES_PER_REPO")
    indexer_required: bool = Field(default=False, alias="INDEXER_REQUIRED")

    # Slack intake behavior
    slack_intake_minimal: bool = Field(default=True, alias="SLACK_INTAKE_MINIMAL")
    slack_require_prompt_confirmation: bool = Field(default=True, alias="SLACK_REQUIRE_PROMPT_CONFIRMATION")
    build_status_heartbeat_seconds: int = Field(default=120, alias="BUILD_STATUS_HEARTBEAT_SECONDS")

    # Code runner strategy:
    # - opencode: run OpenClaw inside worker and open PR directly
    # - native_llm: run in-container LLM coding loop (experimental)
    coderunner_mode: str = Field(default="opencode", alias="CODERUNNER_MODE")
    # opencode execution strategy:
    # - local_openclaw: run OpenClaw locally inside worker container, then commit + open PR
    opencode_execution_mode: str = Field(default="local_openclaw", alias="OPENCODE_EXECUTION_MODE")
    openclaw_auth_dir: str = Field(default="/home/app/.openclaw", alias="OPENCLAW_AUTH_DIR")
    openclaw_auth_seed_dir: str = Field(default="/run/secrets/openclaw", alias="OPENCLAW_AUTH_SEED_DIR")
    opencode_model: str = Field(default="openai-codex/gpt-5.3-codex", alias="OPENCODE_MODEL")
    opencode_timeout_seconds: int = Field(default=1800, alias="OPENCODE_TIMEOUT_SECONDS")
    opencode_keep_temp_agents: bool = Field(default=False, alias="OPENCODE_KEEP_TEMP_AGENTS")
    opencode_no_change_retry_attempts: int = Field(default=1, alias="OPENCODE_NO_CHANGE_RETRY_ATTEMPTS")
    opencode_debug_build: bool = Field(default=False, alias="OPENCODE_DEBUG_BUILD")
    preview_provider: str = Field(default="cloudflare_pages", alias="PREVIEW_PROVIDER")
    cloudflare_pages_project_name: str = Field(default="", alias="CLOUDFLARE_PAGES_PROJECT_NAME")
    cloudflare_pages_production_branch: str = Field(default="main", alias="CLOUDFLARE_PAGES_PRODUCTION_BRANCH")

    # OpenRouter (unified LLM provider with tiered routing)
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_mini_model: str = Field(default="qwen/qwen3.5-9b", alias="OPENROUTER_MINI_MODEL")
    openrouter_frontier_model: str = Field(default="anthropic/claude-opus-4-6", alias="OPENROUTER_FRONTIER_MODEL")
    openrouter_budget_limit_usd: float = Field(default=5.0, alias="OPENROUTER_BUDGET_LIMIT_USD")
    openrouter_referer: str = Field(default="https://github.com/your-org/PRFactory", alias="OPENROUTER_REFERER")
    openrouter_app_title: str = Field(default="PRFactory", alias="OPENROUTER_APP_TITLE")

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
    llm_push_branch_prefix: str = Field(default="prfactory", alias="LLM_PUSH_BRANCH_PREFIX")
    llm_commit_author_name: str = Field(default="prfactory-bot", alias="LLM_COMMIT_AUTHOR_NAME")
    llm_commit_author_email: str = Field(
        default="prfactory-bot@local.invalid",
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

    @staticmethod
    def _looks_like_app_config_token(value: str) -> bool:
        token = (value or "").strip().lower()
        return token.startswith("xoxe.")

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
        if self.indexer_required and not self.repo_indexer_enabled():
            failures.append("INDEXER_REQUIRED=true requires INDEXER_BASE_URL")
        indexer_host = self.indexer_host()
        if self.repo_indexer_enabled() and indexer_host in {"localhost", "127.0.0.1", "::1"}:
            failures.append("INDEXER_BASE_URL must not point to localhost in production")

        if failures:
            joined = "; ".join(failures)
            raise RuntimeError(f"Production security guardrail check failed: {joined}")

    def validate_startup_prerequisites(self) -> None:
        failures: list[str] = []
        bot_token = (self.slack_bot_token or "").strip()
        if bot_token and self._looks_like_app_config_token(bot_token):
            failures.append(
                "SLACK_BOT_TOKEN appears to be a Slack App Configuration token (xoxe.*). "
                "Use a bot token (xoxb-...) or enable Slack OAuth installation flow."
            )
        if self.slack_mode_normalized() == "socket":
            app_token = (self.slack_app_token or "").strip()
            if app_token and not app_token.startswith("xapp-"):
                failures.append("SLACK_APP_TOKEN must be an app-level token (xapp-...) in socket mode")
        if self.github_enabled and self.github_auth_mode_normalized() == "app":
            key_path = (self.github_app_private_key_path or "").strip()
            inline_key = (self.github_app_private_key or "").strip()
            if key_path:
                if not Path(key_path).exists():
                    failures.append(f"GITHUB_APP_PRIVATE_KEY_PATH not found in runtime container: {key_path}")
            elif not inline_key:
                failures.append("GitHub App auth requires GITHUB_APP_PRIVATE_KEY_PATH or GITHUB_APP_PRIVATE_KEY")
        if self.github_user_oauth_enabled():
            if not (self.github_oauth_client_id or "").strip():
                failures.append("GitHub user OAuth requires GITHUB_OAUTH_CLIENT_ID")
            if not (self.github_oauth_client_secret or "").strip():
                failures.append("GitHub user OAuth requires GITHUB_OAUTH_CLIENT_SECRET")
        if self.indexer_required and not self.repo_indexer_enabled():
            failures.append("INDEXER_REQUIRED=true requires INDEXER_BASE_URL")
        if self.enable_slack_bot and self.slack_mode_normalized() == "http":
            if not (self.slack_signing_secret or "").strip():
                failures.append("ENABLE_SLACK_BOT=true and SLACK_MODE=http require SLACK_SIGNING_SECRET")
            if self.slack_oauth_enabled():
                if not (self.slack_client_id or "").strip():
                    failures.append("Slack OAuth requires SLACK_CLIENT_ID")
                if not (self.slack_client_secret or "").strip():
                    failures.append("Slack OAuth requires SLACK_CLIENT_SECRET")
            elif not (self.slack_bot_token or "").strip():
                failures.append(
                    "ENABLE_SLACK_BOT=true and SLACK_MODE=http require SLACK_BOT_TOKEN "
                    "unless Slack OAuth distribution is enabled"
                )
        if (
            not self.mock_mode
            and self.coderunner_mode_normalized() == "opencode"
            and self.opencode_execution_mode_normalized() == "local_openclaw"
        ):
            if shutil.which("openclaw") is None:
                failures.append(
                    "OpenClaw executable not found in runtime container PATH while "
                    "CODERUNNER_MODE=opencode and OPENCODE_EXECUTION_MODE=local_openclaw"
                )
            auth_dir = Path((self.openclaw_auth_dir or "").strip() or "/home/app/.openclaw")
            seed_dir = Path((self.openclaw_auth_seed_dir or "").strip() or "/run/secrets/openclaw")
            auth_candidates = list(auth_dir.glob("agents/*/agent/auth*.json")) if auth_dir.exists() else []
            seed_candidates = list(seed_dir.glob("agents/*/agent/auth*.json")) if seed_dir.exists() else []
            if not auth_candidates and not seed_candidates:
                failures.append(
                    "OpenClaw auth files are missing from both runtime and seed locations: "
                    f"{auth_dir} and {seed_dir}. "
                    "Run scripts/sync_openclaw_auth.ps1 then restart containers."
                )
        if failures:
            raise RuntimeError("; ".join(failures))

    def runtime_diagnostics(self) -> dict[str, object]:
        key_path = (self.github_app_private_key_path or "").strip()
        key_file_exists = bool(key_path) and Path(key_path).exists()
        auth_dir = Path((self.openclaw_auth_dir or "").strip() or "/home/app/.openclaw")
        seed_dir = Path((self.openclaw_auth_seed_dir or "").strip() or "/run/secrets/openclaw")
        auth_candidates = list(auth_dir.glob("agents/*/agent/auth*.json")) if auth_dir.exists() else []
        seed_candidates = list(seed_dir.glob("agents/*/agent/auth*.json")) if seed_dir.exists() else []
        return {
            "app_env": (self.app_env or "").strip().lower() or "local",
            "app_display_name": (self.app_display_name or "").strip() or "PRFactory",
            "auth_mode": self.auth_mode_normalized() or "disabled",
            "mock_mode": bool(self.mock_mode),
            "coderunner_mode": self.coderunner_mode_normalized() or "opencode",
            "opencode_execution_mode": self.opencode_execution_mode_normalized() or "local_openclaw",
            "opencode_debug_build": bool(self.opencode_debug_build),
            "preview_provider": self.preview_provider_normalized() or "cloudflare_pages",
            "cloudflare_pages_project_name": (self.cloudflare_pages_project_name or "").strip(),
            "cloudflare_pages_production_branch": (self.cloudflare_pages_production_branch or "").strip() or "main",
            "docs_enabled": bool(self.docs_enabled()),
            "enable_slack_bot": bool(self.enable_slack_bot),
            "slack_oauth_enabled": bool(self.slack_oauth_enabled()),
            "slack_oauth_install_url": self.slack_oauth_install_url_resolved(),
            "slack_app_redirect_url": self.slack_app_redirect_url_resolved(),
            "slack_require_prompt_confirmation": bool(self.slack_require_prompt_confirmation),
            "build_status_heartbeat_seconds": int(max(self.build_status_heartbeat_seconds, 0)),
            "github_enabled": bool(self.github_enabled),
            "github_auth_mode": self.github_auth_mode_normalized() or "token",
            "github_app_slug": (self.github_app_slug or "").strip(),
            "github_app_install_url": self.github_app_install_url_resolved(),
            "github_app_private_key_configured": bool(key_path or (self.github_app_private_key or "").strip()),
            "github_app_private_key_file_exists": bool(key_file_exists),
            "github_user_oauth_enabled": bool(self.github_user_oauth_enabled()),
            "github_user_oauth_required": bool(self.github_user_oauth_required_effective()),
            "github_oauth_install_url": self.github_oauth_install_url_resolved(),
            "repo_indexer_enabled": bool(self.repo_indexer_enabled()),
            "repo_indexer_required": bool(self.indexer_required),
            "repo_indexer_base_url": self.indexer_base_url_normalized(),
            "repo_indexer_host": self.indexer_host(),
            "workspace_enable_git_clone": bool(self.workspace_enable_git_clone),
            "integration_webhook_configured": bool((self.integration_webhook_secret or "").strip()),
            "openclaw_auth_dir": str(auth_dir),
            "openclaw_auth_dir_exists": bool(auth_dir.exists()),
            "openclaw_auth_files_detected": len(auth_candidates),
            "openclaw_auth_seed_dir": str(seed_dir),
            "openclaw_auth_seed_dir_exists": bool(seed_dir.exists()),
            "openclaw_auth_seed_files_detected": len(seed_candidates),
            "openclaw_cli_available": bool(shutil.which("openclaw")),
            "opencode_no_change_retry_attempts": int(self.opencode_no_change_retry_attempts),
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

    def slack_mode_normalized(self) -> str:
        return (self.slack_mode or "").strip().lower()

    def coderunner_mode_normalized(self) -> str:
        return (self.coderunner_mode or "").strip().lower()

    def opencode_execution_mode_normalized(self) -> str:
        return (self.opencode_execution_mode or "").strip().lower()

    def preview_provider_normalized(self) -> str:
        return (self.preview_provider or "").strip().lower()

    @staticmethod
    def _normalize_route_path(value: str, *, default_path: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            candidate = default_path
        if not candidate.startswith("/"):
            candidate = "/" + candidate
        return candidate

    def slack_oauth_enabled(self) -> bool:
        if self.enable_slack_oauth:
            return True
        return bool((self.slack_client_id or "").strip() and (self.slack_client_secret or "").strip())

    def slack_oauth_install_path_normalized(self) -> str:
        return self._normalize_route_path(self.slack_oauth_install_path, default_path="/api/slack/install")

    def slack_oauth_callback_path_normalized(self) -> str:
        return self._normalize_route_path(self.slack_oauth_callback_path, default_path="/api/slack/oauth/callback")

    def slack_oauth_scopes_list(self) -> list[str]:
        return self._parse_csv(self.slack_oauth_scopes)

    def slack_oauth_user_scopes_list(self) -> list[str]:
        return self._parse_csv(self.slack_oauth_user_scopes)

    def slack_oauth_redirect_uri_resolved(self) -> str:
        explicit = (self.slack_oauth_redirect_uri or "").strip()
        if explicit:
            return explicit
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        return f"{base}{self.slack_oauth_callback_path_normalized()}"

    def slack_oauth_install_url_resolved(self) -> str:
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        return f"{base}{self.slack_oauth_install_path_normalized()}"

    def slack_app_redirect_url_resolved(self) -> str:
        app_id = (self.slack_app_id or "").strip()
        if not app_id:
            return ""
        params = {"app": app_id}
        team_id = (self.slack_team_id or "").strip()
        if team_id:
            params["team"] = team_id
        return "https://slack.com/app_redirect?" + urlencode(params)

    def service_auth_group_set(self) -> set[str]:
        return {g.lower() for g in self._parse_csv(self.service_auth_groups)}

    def rbac_requester_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_requesters)

    def rbac_builder_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_builders)

    def rbac_approver_rules(self) -> list[str]:
        return self._parse_csv(self.rbac_approvers)

    def github_app_install_url_resolved(self) -> str:
        explicit = (self.github_app_install_url or "").strip()
        if explicit:
            return explicit
        slug = (self.github_app_slug or "").strip()
        if slug:
            return f"https://github.com/apps/{slug}/installations/new"
        return ""

    def github_user_oauth_enabled(self) -> bool:
        if self.enable_github_user_oauth:
            return True
        return bool((self.github_oauth_client_id or "").strip() and (self.github_oauth_client_secret or "").strip())

    def github_user_oauth_required_effective(self) -> bool:
        if not self.github_user_oauth_enabled():
            return False
        return bool(self.github_user_oauth_required)

    def github_oauth_scopes_list(self) -> list[str]:
        return self._parse_csv(self.github_oauth_scopes)

    def github_oauth_install_path_normalized(self) -> str:
        return self._normalize_route_path(self.github_oauth_install_path, default_path="/api/github/install")

    def github_oauth_callback_path_normalized(self) -> str:
        return self._normalize_route_path(self.github_oauth_callback_path, default_path="/api/github/oauth/callback")

    def github_oauth_redirect_uri_resolved(self) -> str:
        explicit = (self.github_oauth_redirect_uri or "").strip()
        if explicit:
            return explicit
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        return f"{base}{self.github_oauth_callback_path_normalized()}"

    def github_oauth_install_url_resolved(self) -> str:
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return ""
        return f"{base}{self.github_oauth_install_path_normalized()}"

    def github_oauth_install_url_for_user(self, *, slack_user_id: str, slack_team_id: str = "", next_url: str = "") -> str:
        base = self.github_oauth_install_url_resolved()
        if not base:
            return ""
        params: dict[str, str] = {}
        if (slack_user_id or "").strip():
            params["slack_user_id"] = slack_user_id.strip()
        if (slack_team_id or "").strip():
            params["slack_team_id"] = slack_team_id.strip()
        if (next_url or "").strip():
            params["next"] = next_url.strip()
        if not params:
            return base
        return f"{base}?{urlencode(params)}"

    def indexer_base_url_normalized(self) -> str:
        return (self.indexer_base_url or "").strip().rstrip("/")

    def indexer_host(self) -> str:
        base = self.indexer_base_url_normalized()
        if not base:
            return ""
        try:
            parsed = urlsplit(base)
            return (parsed.hostname or "").strip().lower()
        except Exception:
            return ""

    def repo_indexer_enabled(self) -> bool:
        return bool(self.indexer_base_url_normalized())


@lru_cache
def get_settings() -> Settings:
    return Settings()
