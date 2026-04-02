"""Slack Block Kit builders for the PRFactory intake flow.

Pure JSON-building functions extracted from slackbot.py.
None of these call the Slack client or mutate session state.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.services.llm_costs import aggregate_llm_costs, build_llm_cost_context_block


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_OPTION_NONE = "__NONE__"
REPO_OPTION_NEW = "__NEW__"
REPO_OPTION_CONNECT = "__CONNECT__"
BRANCH_OPTION_NONE = "__NONE__"
BRANCH_OPTION_NEW = "__NEW__"
BRANCH_OPTION_AUTOGEN = "__AUTOGEN__"
OPENROUTER_MINI_MODEL_DEFAULT = "qwen/qwen3.5-9b"
OPENROUTER_FRONTIER_MODEL_DEFAULT = "anthropic/claude-opus-4-6"

INTAKE_MODE_NORMAL = "normal"
INTAKE_MODE_DEVELOPER = "developer"

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "How can I help you?",
    "problem": "Describe what you want in one short paragraph (what to build + why).",
    "business_justification": "Why is this needed now?",
    "links": "Optional: share links/files in this thread, or reply `skip`.",
    "repo": "Do you know what project/repo this belongs to? Reply with `org/repo`, repo URL, or `unsure`.",
    "base_branch": "Optional: which base branch should we open the PR against? Reply with branch name, or `skip`.",
    "implementation_mode": "Should implementation start from scratch or reuse existing project patterns? Reply `scratch` or `reuse`.",
    "source_repos": "If reusing existing patterns, which repos should be references? One per line.",
    "edit_scope": "For edit mode, what files/modules/symbols should I touch first? (one short reply, or `skip`)",
    "proposed_solution": "Any preferred implementation approach or constraints? Reply `skip` if none.",
    "acceptance_criteria": "Optional: acceptance criteria, one per line. Reply `skip` to use defaults.",
}


# ---------------------------------------------------------------------------
# Small helpers used by block builders
# ---------------------------------------------------------------------------

def normalize_intake_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {INTAKE_MODE_DEVELOPER}:
        return INTAKE_MODE_DEVELOPER
    return INTAKE_MODE_NORMAL


def intake_mode_label(mode: str) -> str:
    return "Developer" if normalize_intake_mode(mode) == INTAKE_MODE_DEVELOPER else "Normal"


def intake_mode_toggle_label(mode: str) -> str:
    if normalize_intake_mode(mode) == INTAKE_MODE_DEVELOPER:
        return "Switch to Normal"
    return "Switch to Developer"


def normalize_router_field_name(field_name: str) -> str:
    normalized = str(field_name or "").strip().lower()
    alias_map = {
        "branch": "base_branch",
        "description": "problem",
    }
    return alias_map.get(normalized, normalized)


def normalize_user_skill(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"developer", "non_technical"}:
        return normalized
    return "technical"


def format_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m == "reuse_existing":
        return "Reuse existing patterns"
    return "New feature"


def slugify_ref(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    parts = slug.split("-")[:6]
    return "-".join(parts)[:32]


def feature_reference(*, feature_id: str, title: str) -> str:
    slug = slugify_ref(title)
    if not slug:
        return str(feature_id or "")
    return f"{slug}-{str(feature_id or '')[:8]}"


def _status_emoji(status: str) -> str:
    return {
        "NEW": ":new:",
        "NEEDS_INFO": ":question:",
        "READY_FOR_BUILD": ":white_check_mark:",
        "BUILDING": ":hammer_and_wrench:",
        "PR_OPENED": ":git-pull-request:",
        "PREVIEW_READY": ":eyes:",
        "PRODUCT_APPROVED": ":heavy_check_mark:",
        "READY_TO_MERGE": ":rocket:",
        "MERGED": ":tada:",
        "FAILED_SPEC": ":x:",
        "FAILED_BUILD": ":x:",
        "FAILED_PREVIEW": ":warning:",
        "NEEDS_HUMAN": ":bust_in_silhouette:",
    }.get(str(status or "").strip().upper(), ":grey_question:")


def build_app_home_blocks(
    *,
    app_name: str,
    user_id: str,
    recent_features: list[dict[str, Any]],
    github_status: str,
    total_cost: float = 0.0,
    slash_command: str = "/prfactory",
    new_request_url: str = "",
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for the Slack App Home tab."""

    del user_id

    intro_block: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Run `{slash_command}` in any channel to start a new feature request.",
        },
    }
    if new_request_url:
        intro_block["accessory"] = {
            "type": "button",
            "text": {"type": "plain_text", "text": "New request"},
            "url": new_request_url,
        }

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{app_name}"},
        },
        intro_block,
        {"type": "divider"},
    ]

    if recent_features:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Your recent requests*"},
            }
        )
        for feature in recent_features[:5]:
            status = str(feature.get("status") or "UNKNOWN").strip() or "UNKNOWN"
            title = str(feature.get("title") or "(untitled)").strip() or "(untitled)"
            feature_id = str(feature.get("id") or "").strip()
            emoji = _status_emoji(status)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{emoji} *{title}*\n`{feature_id}` - {status}",
                    },
                }
            )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No feature requests yet. Run the slash command to get started!_",
                },
            }
        )

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"GitHub: {github_status}"},
            ],
        }
    )
    if float(total_cost or 0.0) > 0:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":moneybag: Total OpenRouter spend: ${float(total_cost):.4f}",
                    }
                ],
            }
        )
    return blocks


def parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_elapsed(seconds: int) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def build_progress_text(feature: dict[str, Any]) -> str:
    status = str(feature.get("status") or "").strip()
    if status != "BUILDING":
        return ""

    runs = feature.get("runs") or []
    active_job_id = str(feature.get("active_build_job_id") or "").strip()
    candidate_run: dict[str, Any] | None = None
    if active_job_id:
        for run in runs:
            if not isinstance(run, dict):
                continue
            if str(run.get("runner_run_id") or "").strip() == active_job_id:
                candidate_run = run
                break
    if candidate_run is None:
        for run in reversed(runs):
            if not isinstance(run, dict):
                continue
            if str(run.get("status") or "").strip().upper() in {"RUNNING", "QUEUED"}:
                candidate_run = run
                break

    now = datetime.now(timezone.utc)
    started_at = parse_iso_datetime((candidate_run or {}).get("started_at")) or parse_iso_datetime(feature.get("updated_at"))
    if not started_at:
        started_at = parse_iso_datetime(feature.get("created_at")) or now
    elapsed_text = format_elapsed(int((now - started_at).total_seconds()))

    last_signal = parse_iso_datetime((candidate_run or {}).get("updated_at"))
    events = feature.get("events") or []
    if not last_signal and isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            maybe = parse_iso_datetime(event.get("created_at"))
            if maybe:
                last_signal = maybe
                break
    signal_text = ""
    if last_signal:
        signal_text = f" | Last signal `{format_elapsed(int((now - last_signal).total_seconds()))}` ago"
    return f"Build runtime: `{elapsed_text}`{signal_text}"


def openrouter_enabled(settings: Any) -> bool:
    return bool(str(getattr(settings, "openrouter_api_key", "") or "").strip())


def display_model_name(model_name: str) -> str:
    name = str(model_name or "").strip()
    if "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


def resolved_model_name(settings: Any, *, tier: str, model_name: str = "") -> str:
    explicit = str(model_name or "").strip()
    if explicit:
        return explicit
    tier_normalized = str(tier or "").strip().lower()
    if tier_normalized == "frontier":
        return str(
            getattr(settings, "openrouter_frontier_model", OPENROUTER_FRONTIER_MODEL_DEFAULT)
            or OPENROUTER_FRONTIER_MODEL_DEFAULT
        ).strip()
    return str(
        getattr(settings, "openrouter_mini_model", OPENROUTER_MINI_MODEL_DEFAULT)
        or OPENROUTER_MINI_MODEL_DEFAULT
    ).strip()


# ---------------------------------------------------------------------------
# Block Kit builders
# ---------------------------------------------------------------------------

def intake_controls_blocks(*, mode: str) -> list[dict[str, Any]]:
    current_mode = normalize_intake_mode(mode)
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "ff_toggle_mode",
                    "text": {"type": "plain_text", "text": intake_mode_toggle_label(current_mode)},
                    "value": current_mode,
                },
                {
                    "type": "button",
                    "action_id": "ff_show_help",
                    "text": {"type": "plain_text", "text": "Help"},
                    "value": "help",
                },
            ],
        }
    ]


def title_prompt_blocks(
    *,
    mode: str,
    seed_prompt: str = "",
    github_status_block: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_seed = str(seed_prompt or "").strip()
    if normalized_seed:
        preview = normalized_seed[:180]
        blocks = [
            {
                "type": "section",
                "text": {"type": "plain_text", "text": "What should this request be titled?"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Captured prompt: `{preview}`"},
                    {"type": "mrkdwn", "text": "Reply with a short title in this thread."},
                ],
            },
        ]
        if github_status_block is not None:
            blocks.append(github_status_block)
        return [*blocks, *intake_controls_blocks(mode=mode)]
    blocks = [
        {
            "type": "section",
            "text": {"type": "plain_text", "text": QUESTION_BY_FIELD["title"]},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Enter what you want to build, then reply in this thread."},
            ],
        },
    ]
    if github_status_block is not None:
        blocks.append(github_status_block)
    return [*blocks, *intake_controls_blocks(mode=mode)]


def model_indicator_block(settings: Any, *, tier: str, model_name: str = "") -> dict[str, Any] | None:
    if not openrouter_enabled(settings):
        return None
    resolved = display_model_name(resolved_model_name(settings, tier=tier, model_name=model_name))
    if not resolved:
        return None
    label = ":rocket: _Analyzed by {model}_" if str(tier or "").strip().lower() == "frontier" else ":zap: _Assisted by {model}_"
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": label.format(model=resolved),
            }
        ],
    }


def thread_blocks_with_cost_summary(text: str, events: list[Any]) -> list[dict[str, Any]] | None:
    summary = aggregate_llm_costs(events)
    context_block = build_llm_cost_context_block(summary)
    if not context_block:
        return None
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        context_block,
    ]


def nontechnical_help_block(field_name: str) -> dict[str, Any] | None:
    normalized = normalize_router_field_name(field_name)
    help_text_by_field = {
        "repo": ":bulb: _A repository is where the code lives. Pick the one that matches your project._",
        "base_branch": ":bulb: _A branch is the starting version of the code. If you're unsure, the default branch is usually best._",
    }
    help_text = help_text_by_field.get(normalized, "")
    if not help_text:
        return None
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": help_text,
            }
        ],
    }


def feature_message_blocks(feature: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    fid = feature["id"]
    status = feature["status"]
    title = feature["title"]
    ref = feature_reference(feature_id=fid, title=title)
    spec = feature.get("spec") or {}
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    mode_label = format_mode(mode)
    preview = feature.get("preview_url") or ""
    pr = feature.get("github_pr_url") or ""
    repo_hint = str(spec.get("repo") or "").strip()
    validation = spec.get("_validation") or {}
    missing = validation.get("missing") or []
    missing_summary = ", ".join(missing) if missing else "none"
    progress_text = build_progress_text(feature)

    actions: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": "ff_add_details",
            "text": {"type": "plain_text", "text": "Add more context"},
            "value": fid,
        },
    ]
    if status == "BUILDING":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_refresh_status",
                "text": {"type": "plain_text", "text": "Refresh status"},
                "value": fid,
            }
        )
    if status == "READY_FOR_BUILD":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_run_build",
                "text": {"type": "plain_text", "text": "Run build"},
                "style": "primary",
                "value": fid,
            }
        )
    if status == "PREVIEW_READY":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_approve",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "danger",
                "value": fid,
            }
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{title}*\nStatus: `{status}`\nMode: `{mode}` ({mode_label})\nRef: `{ref}`\nID: `{fid}`\n"
                    f"Missing details: `{missing_summary}`"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Repo: {repo_hint or '(none)'} | PR: {pr or '(pending)'} | "
                        f"Preview: {preview or '(none)'}"
                    ),
                },
                *(
                    [{"type": "mrkdwn", "text": progress_text}]
                    if progress_text
                    else []
                ),
            ],
        },
        {"type": "actions", "elements": actions},
    ]


def github_prompt_blocks(
    *,
    user_id: str,
    team_id: str,
    mode: str,
    oauth_url: str,
) -> list[dict[str, Any]]:
    if mode == "reauth":
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *Your GitHub connection has expired.*\n"
                        "This happens periodically. Click below to reconnect - it takes about 10 seconds."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reconnect GitHub"},
                        "action_id": "ff_github_reauth",
                        "style": "primary",
                        "url": oauth_url,
                    }
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            ":hourglass_flowing_sand: _I'll continue collecting your request details. "
                            "Once you reconnect, I'll show your repos._"
                        ),
                    }
                ],
            },
        ]
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":link: *Let's connect your GitHub account.*\n"
                    "This lets me show your real repos and branches, and create PRs in the right place."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Connect GitHub"},
                    "action_id": "ff_github_connect",
                    "style": "primary",
                    "url": oauth_url,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":bulb: _You can also type a repo name manually if you prefer._",
                }
            ],
        },
    ]


def slack_option(*, text: str, value: str) -> dict[str, Any]:
    option_text = (text or "").strip() or value
    option_value = (value or "").strip() or option_text
    return {
        "text": {"type": "plain_text", "text": option_text[:75]},
        "value": option_value[:200],
    }


def fallback_repo_options() -> list[dict[str, Any]]:
    return [
        slack_option(text="None (use defaults)", value=REPO_OPTION_NONE),
        slack_option(text="New repo (I will type it)", value=REPO_OPTION_NEW),
    ]


def fallback_branch_options() -> list[dict[str, Any]]:
    return [
        slack_option(text="Auto-create PRFactory branch (recommended)", value=BRANCH_OPTION_AUTOGEN),
        slack_option(text="None (use default base branch)", value=BRANCH_OPTION_NONE),
        slack_option(text="Type existing base branch", value=BRANCH_OPTION_NEW),
    ]


def dedupe_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for option in options:
        value = str((option.get("value") or "")).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def initial_option(options: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    for option in options:
        if str(option.get("value") or "").strip() == normalized:
            return option
    return None


def developer_mode_repo_blocks(
    *,
    options: list[dict[str, Any]],
    prompt_text: str = "Target repo (optional).",
    placeholder_text: str = "Select repo",
    initial_value: str = "",
) -> list[dict[str, Any]]:
    accessory: dict[str, Any] = {
        "type": "static_select",
        "action_id": "ff_repo_select",
        "placeholder": {"type": "plain_text", "text": placeholder_text},
        "options": options[:100],
    }
    initial = initial_option(options, initial_value)
    if initial is not None:
        accessory["initial_option"] = initial
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": prompt_text,
            },
            "accessory": accessory,
        }
    ]


def build_spec_summary_blocks(
    spec: dict[str, Any],
    *,
    feature_ref: str = "",
) -> list[dict[str, Any]]:
    """Build a rich summary of the captured spec for user review."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Feature Request Summary"},
        },
    ]

    field_display = [
        ("Title", spec.get("title")),
        ("Problem", spec.get("problem")),
        ("Repository", spec.get("repo")),
        ("Branch", spec.get("base_branch")),
        ("Mode", spec.get("implementation_mode")),
        ("Acceptance Criteria", spec.get("acceptance_criteria")),
    ]

    for label, value in field_display:
        if not value:
            continue
        if isinstance(value, list):
            value = "\n".join(f"\u2022 {item}" for item in value)
        text = f"*{label}:*\n{value}"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text[:3000]},
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Looks good, create it"},
                "action_id": "ff_confirm_spec",
                "style": "primary",
                "value": "confirm",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Edit a field"},
                "action_id": "ff_edit_field",
                "value": "edit",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Cancel"},
                "action_id": "ff_cancel_intake",
                "value": "cancel",
            },
        ],
    })

    return blocks


def developer_mode_branch_blocks(
    *,
    repo_slug: str,
    options: list[dict[str, Any]],
    prompt_text: str = "",
    placeholder_text: str = "Select branch",
    initial_value: str = "",
) -> list[dict[str, Any]]:
    repo_text = repo_slug or "(none)"
    section_text = prompt_text or f"Base branch for `{repo_text}` (optional)."
    accessory: dict[str, Any] = {
        "type": "static_select",
        "action_id": "ff_branch_select",
        "placeholder": {"type": "plain_text", "text": placeholder_text},
        "options": options[:100],
    }
    initial = initial_option(options, initial_value)
    if initial is not None:
        accessory["initial_option"] = initial
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": section_text,
            },
            "accessory": accessory,
        }
    ]
