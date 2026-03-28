from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib import error, request
from urllib.parse import urlencode


DEFAULT_DISPLAY_NAME = "PRFactory"
EVENTS_ENDPOINT_SUFFIX = "/api/slack/events"
DEFAULT_OAUTH_CALLBACK_PATH = "/api/slack/oauth/callback"
REQUIRED_BOT_EVENTS = [
    "app_home_opened",
    "member_joined_channel",
    "message.channels",
    "message.groups",
    "message.im",
    "message.mpim",
]
REQUIRED_COMMANDS = [
    {
        "command": "/prfactory",
        "description": "Create a PR request",
        "usage_hint": "Add invoice export",
        "should_escape": False,
    },
    {
        "command": "/feature",
        "description": "Legacy alias for /prfactory",
        "usage_hint": "Add invoice export",
        "should_escape": False,
    },
    {
        "command": "/prfactory-github",
        "description": "GitHub app install instructions",
        "usage_hint": "Connect GitHub",
        "should_escape": False,
    },
    {
        "command": "/prfactory-indexer",
        "description": "Search indexed repositories",
        "usage_hint": "webhook retries",
        "should_escape": False,
    },
]


def _read_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in data:
            data[key] = value
    return data


def _env_or_arg(*, value: str, env: dict[str, str], key: str) -> str:
    if value and value.strip():
        return value.strip()
    return (env.get(key, "") or "").strip()


def _events_url(base_url: str) -> str:
    return base_url.rstrip("/") + EVENTS_ENDPOINT_SUFFIX


def _oauth_callback_url(base_url: str, callback_path: str) -> str:
    path = (callback_path or "").strip() or DEFAULT_OAUTH_CALLBACK_PATH
    if not path.startswith("/"):
        path = "/" + path
    return base_url.rstrip("/") + path


def _slack_api_call(
    *,
    token: str,
    method: str,
    payload: dict[str, Any],
    form_encoded: bool = False,
) -> dict[str, Any]:
    if form_encoded:
        body = urlencode({k: str(v) for k, v in payload.items()}).encode("utf-8")
        content_type = "application/x-www-form-urlencoded; charset=utf-8"
    else:
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    req = request.Request(
        url=f"https://slack.com/api/{method}",
        method="POST",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack API {method} failed with HTTP {exc.code}: {response_body}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Slack API {method} request failed: {exc}") from exc

    try:
        payload_json = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Slack API {method} returned non-JSON response: {body}") from exc

    if not isinstance(payload_json, dict) or not payload_json.get("ok"):
        error_code = ""
        needed = ""
        if isinstance(payload_json, dict):
            error_code = str(payload_json.get("error") or "").strip()
            needed = str(payload_json.get("needed") or "").strip()
        hint = f" needed={needed}" if needed else ""
        raise RuntimeError(f"Slack API {method} error: {error_code or 'unknown_error'}{hint}")
    return payload_json


def _rotate_config_token(*, token: str, refresh_token: str) -> tuple[str, str]:
    normalized_refresh = (refresh_token or "").strip()
    if not normalized_refresh:
        return token, refresh_token

    rotate_payload = {"refresh_token": normalized_refresh}
    bearer_candidates: list[str] = []
    for candidate in [token, normalized_refresh]:
        value = str(candidate or "").strip()
        if value and value not in bearer_candidates:
            bearer_candidates.append(value)

    last_error: Exception | None = None
    for bearer in bearer_candidates:
        try:
            rotated = _slack_api_call(
                token=bearer,
                method="tooling.tokens.rotate",
                payload=rotate_payload,
                form_encoded=True,
            )
            new_access = str(rotated.get("token") or "").strip()
            new_refresh = str(rotated.get("refresh_token") or "").strip()
            if not new_access or not new_refresh:
                raise RuntimeError("tooling.tokens.rotate returned missing token fields.")
            return new_access, new_refresh
        except Exception as e:  # noqa: BLE001
            last_error = e

    if last_error is not None:
        raise RuntimeError(f"Slack App Configuration token rotation failed: {last_error}") from last_error
    return token, refresh_token


def _write_dotenv_values(path: Path, updates: dict[str, str]) -> None:
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    for key, value in updates.items():
        replaced = False
        for idx, line in enumerate(existing_lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            current_key = line.split("=", 1)[0].strip()
            if current_key != key:
                continue
            existing_lines[idx] = f"{key}={value}"
            replaced = True
            break
        if not replaced:
            existing_lines.append(f"{key}={value}")

    output = "\n".join(existing_lines).rstrip() + "\n"
    path.write_text(output, encoding="utf-8")


def _ensure_bot_events(existing: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for event in existing + REQUIRED_BOT_EVENTS:
        token = str(event or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _ensure_slash_commands(existing: list[dict[str, Any]], *, request_url: str) -> list[dict[str, Any]]:
    by_command: dict[str, dict[str, Any]] = {}
    trailing: list[dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            trailing.append(dict(item))
            continue
        by_command[command] = dict(item)

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for required in REQUIRED_COMMANDS:
        command = str(required["command"]).strip()
        merged = dict(by_command.get(command) or {})
        merged.update(required)
        merged["url"] = request_url
        output.append(merged)
        seen.add(command)

    for command, cfg in by_command.items():
        if command in seen:
            continue
        output.append(cfg)

    output.extend(trailing)
    return output


def _patched_manifest(
    *,
    manifest: dict[str, Any],
    request_url: str,
    oauth_callback_url: str,
    display_name: str,
) -> dict[str, Any]:
    patched = dict(manifest)

    display_information = dict(patched.get("display_information") or {})
    display_information["name"] = display_name
    if not str(display_information.get("description") or "").strip():
        display_information["description"] = "Slack bot for AI-assisted PR automation"
    patched["display_information"] = display_information

    features = dict(patched.get("features") or {})
    bot_user = dict(features.get("bot_user") or {})
    bot_user["display_name"] = display_name
    if "always_online" not in bot_user:
        bot_user["always_online"] = False
    features["bot_user"] = bot_user
    features["slash_commands"] = _ensure_slash_commands(
        [x for x in (features.get("slash_commands") or []) if isinstance(x, dict)],
        request_url=request_url,
    )
    patched["features"] = features

    oauth_config = dict(patched.get("oauth_config") or {})
    oauth_config["redirect_urls"] = [oauth_callback_url]
    patched["oauth_config"] = oauth_config

    settings = dict(patched.get("settings") or {})
    event_subscriptions = dict(settings.get("event_subscriptions") or {})
    event_subscriptions["request_url"] = request_url
    event_subscriptions["bot_events"] = _ensure_bot_events(
        [str(x) for x in (event_subscriptions.get("bot_events") or []) if str(x).strip()]
    )
    settings["event_subscriptions"] = event_subscriptions

    interactivity = dict(settings.get("interactivity") or {})
    interactivity["is_enabled"] = True
    interactivity["request_url"] = request_url
    settings["interactivity"] = interactivity
    settings["socket_mode_enabled"] = False
    patched["settings"] = settings
    return patched


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Slack app manifest URLs/events/commands so deploys do not require manual Slack reconfiguration."
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file.")
    parser.add_argument("--base-url", default="", help="Public base URL, e.g. https://<modal-url>.")
    parser.add_argument("--app-id", default="", help="Slack app id (falls back to SLACK_APP_ID).")
    parser.add_argument(
        "--config-token",
        default="",
        help="Slack App Configuration token (xoxe.xoxp-..., falls back to SLACK_APP_CONFIG_TOKEN).",
    )
    parser.add_argument(
        "--config-refresh-token",
        default="",
        help=(
            "Slack App Configuration refresh token "
            "(falls back to SLACK_APP_CONFIG_REFRESH_TOKEN)."
        ),
    )
    parser.add_argument("--display-name", default="", help="Slack app display name (default PRFactory).")
    parser.add_argument(
        "--oauth-callback-path",
        default="",
        help="OAuth callback path (defaults to SLACK_OAUTH_CALLBACK_PATH or /api/slack/oauth/callback).",
    )
    parser.add_argument(
        "--skip-token-rotation",
        action="store_true",
        help="Skip tooling.tokens.rotate for the App Configuration token.",
    )
    parser.add_argument(
        "--write-rotated-token-to-env",
        action="store_true",
        help=(
            "Write rotated SLACK_APP_CONFIG_TOKEN and SLACK_APP_CONFIG_REFRESH_TOKEN "
            "back to --env-file."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print patched manifest JSON and exit.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    env = _read_dotenv(Path(args.env_file))

    base_url = _env_or_arg(value=args.base_url, env=env, key="BASE_URL")
    app_id = _env_or_arg(value=args.app_id, env=env, key="SLACK_APP_ID")
    config_token = _env_or_arg(value=args.config_token, env=env, key="SLACK_APP_CONFIG_TOKEN")
    config_refresh_token = _env_or_arg(
        value=args.config_refresh_token,
        env=env,
        key="SLACK_APP_CONFIG_REFRESH_TOKEN",
    )
    display_name = _env_or_arg(value=args.display_name, env=env, key="APP_DISPLAY_NAME") or DEFAULT_DISPLAY_NAME
    callback_path = _env_or_arg(
        value=args.oauth_callback_path,
        env=env,
        key="SLACK_OAUTH_CALLBACK_PATH",
    ) or DEFAULT_OAUTH_CALLBACK_PATH

    missing: list[str] = []
    if not base_url:
        missing.append("BASE_URL/--base-url")
    if not app_id:
        missing.append("SLACK_APP_ID/--app-id")
    if not config_token and not config_refresh_token:
        missing.append("SLACK_APP_CONFIG_TOKEN or SLACK_APP_CONFIG_REFRESH_TOKEN")
    if missing:
        raise RuntimeError(
            "Missing required inputs: "
            + ", ".join(missing)
            + ". Set them in .env or pass CLI flags."
        )

    if not args.skip_token_rotation and config_refresh_token:
        token_for_rotation = config_token or config_refresh_token
        rotated_access = ""
        rotated_refresh = ""
        try:
            rotated_access, rotated_refresh = _rotate_config_token(
                token=token_for_rotation,
                refresh_token=config_refresh_token,
            )
        except RuntimeError as exc:
            if config_token:
                print(f"WARN: {exc}. Continuing with provided config token.", file=sys.stderr)
            else:
                raise
        if rotated_access and rotated_refresh:
            config_token = rotated_access
            config_refresh_token = rotated_refresh
            if args.write_rotated_token_to_env:
                env_file_path = Path(args.env_file)
                _write_dotenv_values(
                    env_file_path,
                    {
                        "SLACK_APP_CONFIG_TOKEN": config_token,
                        "SLACK_APP_CONFIG_REFRESH_TOKEN": config_refresh_token,
                    },
                )

    if not config_token:
        raise RuntimeError("No usable Slack App Configuration access token available after rotation attempt.")

    request_url = _events_url(base_url)
    oauth_callback_url = _oauth_callback_url(base_url, callback_path)
    export_resp = _slack_api_call(
        token=config_token,
        method="apps.manifest.export",
        payload={"app_id": app_id},
    )
    manifest = export_resp.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("Slack API apps.manifest.export did not return a valid manifest object.")

    patched = _patched_manifest(
        manifest=manifest,
        request_url=request_url,
        oauth_callback_url=oauth_callback_url,
        display_name=display_name,
    )
    _slack_api_call(token=config_token, method="apps.manifest.validate", payload={"manifest": patched})

    if args.dry_run:
        print(json.dumps(patched, indent=2, sort_keys=True))
        return 0

    _slack_api_call(
        token=config_token,
        method="apps.manifest.update",
        payload={"app_id": app_id, "manifest": patched},
    )
    print(f"Slack manifest synced for app {app_id}.")
    print(f"Events URL: {request_url}")
    print(f"Slash commands URL: {request_url}")
    print(f"OAuth callback URL: {oauth_callback_url}")
    print("If scopes changed, reinstall/re-authorize the Slack app in your workspace.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
