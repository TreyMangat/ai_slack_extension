from __future__ import annotations

import json
import time
from typing import Any

import httpx
from rich.console import Console

from app.config import get_settings
from app.services.reviewer_service import is_approver_allowed


console = Console()

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "What should we call this feature?",
    "problem": "What user problem should this solve?",
    "business_justification": "Why is this needed now and what outcome do you expect?",
    "acceptance_criteria": "How will we know this is complete? Please share acceptance criteria.",
    "source_repos": "Which existing repos should be used as reference snapshots?",
    "implementation_mode": "Should this be built from scratch or reuse existing repos?",
}


def _parse_lines(text: str) -> list[str]:
    return [line.strip().lstrip("- ") for line in (text or "").splitlines() if line.strip()]


def _value(values: dict[str, Any], block_id: str) -> str:
    return values.get(block_id, {}).get("value", {}).get("value", "")


def _selected(values: dict[str, Any], block_id: str) -> str:
    return values.get(block_id, {}).get("value", {}).get("selected_option", {}).get("value", "")


def _format_mode(mode: str) -> str:
    if mode == "reuse_existing":
        return "Reuse existing repo patterns"
    return "Build from scratch"


def _extract_spec_from_form(values: dict[str, Any]) -> dict[str, Any]:
    mode = _selected(values, "mode").strip() or "new_feature"
    return {
        "title": _value(values, "title").strip(),
        "problem": _value(values, "problem").strip(),
        "business_justification": _value(values, "why").strip(),
        "implementation_mode": mode,
        "source_repos": _parse_lines(_value(values, "source_repos")),
        "proposed_solution": _value(values, "solution").strip(),
        "acceptance_criteria": _parse_lines(_value(values, "acceptance")),
        "non_goals": _parse_lines(_value(values, "non_goals")),
        "repo": _value(values, "repo").strip(),
        "risk_flags": [x.strip() for x in _value(values, "risk").split(",") if x.strip()],
        "links": _parse_lines(_value(values, "links")),
    }


def _build_feature_modal(initial_title: str, channel_id: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "ff_feature_submit",
        "private_metadata": json.dumps({"channel_id": channel_id}),
        "title": {"type": "plain_text", "text": "New feature"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "title",
                "label": {"type": "plain_text", "text": "What should we build?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": initial_title[:150] if initial_title else "",
                },
            },
            {
                "type": "input",
                "block_id": "problem",
                "optional": True,
                "label": {"type": "plain_text", "text": "What problem are users facing?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "why",
                "optional": True,
                "label": {"type": "plain_text", "text": "Why is this needed now?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "mode",
                "label": {"type": "plain_text", "text": "How should implementation start?"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "initial_option": {
                        "text": {"type": "plain_text", "text": "Build from scratch"},
                        "value": "new_feature",
                    },
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Build from scratch"},
                            "value": "new_feature",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Reuse existing repo patterns"},
                            "value": "reuse_existing",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "source_repos",
                "optional": True,
                "label": {"type": "plain_text", "text": "Source repos (one per line for reuse mode)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "org/existing-repo\nhttps://github.com/org/another-repo",
                    },
                },
            },
            {
                "type": "input",
                "block_id": "acceptance",
                "optional": True,
                "label": {"type": "plain_text", "text": "Acceptance criteria (one per line)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "solution",
                "optional": True,
                "label": {"type": "plain_text", "text": "Suggested solution (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "non_goals",
                "optional": True,
                "label": {"type": "plain_text", "text": "Non-goals (one per line, optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "repo",
                "optional": True,
                "label": {"type": "plain_text", "text": "Primary target repo (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                },
            },
            {
                "type": "input",
                "block_id": "risk",
                "optional": True,
                "label": {"type": "plain_text", "text": "Risk flags (optional, comma-separated)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                },
            },
            {
                "type": "input",
                "block_id": "links",
                "optional": True,
                "label": {"type": "plain_text", "text": "Links (one per line, optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                },
            },
        ],
    }


def _build_update_modal(
    feature: dict[str, Any],
    *,
    channel_id: str,
    message_ts: str,
    thread_ts: str,
) -> dict[str, Any]:
    spec = feature.get("spec") or {}
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"

    return {
        "type": "modal",
        "callback_id": "ff_feature_update",
        "private_metadata": json.dumps(
            {
                "feature_id": feature["id"],
                "channel_id": channel_id,
                "message_ts": message_ts,
                "thread_ts": thread_ts,
            }
        ),
        "title": {"type": "plain_text", "text": "Update details"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "title",
                "label": {"type": "plain_text", "text": "What should we build?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.get("title", ""))[:150],
                },
            },
            {
                "type": "input",
                "block_id": "problem",
                "optional": True,
                "label": {"type": "plain_text", "text": "What problem are users facing?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": str(spec.get("problem", "")),
                },
            },
            {
                "type": "input",
                "block_id": "why",
                "optional": True,
                "label": {"type": "plain_text", "text": "Why is this needed now?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": str(spec.get("business_justification", "")),
                },
            },
            {
                "type": "input",
                "block_id": "mode",
                "label": {"type": "plain_text", "text": "How should implementation start?"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "initial_option": {
                        "text": {"type": "plain_text", "text": _format_mode(mode)},
                        "value": mode,
                    },
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Build from scratch"},
                            "value": "new_feature",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Reuse existing repo patterns"},
                            "value": "reuse_existing",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "source_repos",
                "optional": True,
                "label": {"type": "plain_text", "text": "Source repos (one per line for reuse mode)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": "\n".join([str(x) for x in spec.get("source_repos", []) if str(x).strip()]),
                },
            },
            {
                "type": "input",
                "block_id": "acceptance",
                "optional": True,
                "label": {"type": "plain_text", "text": "Acceptance criteria (one per line)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": "\n".join(
                        [str(x) for x in spec.get("acceptance_criteria", []) if str(x).strip()]
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "solution",
                "optional": True,
                "label": {"type": "plain_text", "text": "Suggested solution (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": str(spec.get("proposed_solution", "")),
                },
            },
            {
                "type": "input",
                "block_id": "non_goals",
                "optional": True,
                "label": {"type": "plain_text", "text": "Non-goals (one per line, optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": "\n".join([str(x) for x in spec.get("non_goals", []) if str(x).strip()]),
                },
            },
            {
                "type": "input",
                "block_id": "repo",
                "optional": True,
                "label": {"type": "plain_text", "text": "Primary target repo (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.get("repo", "")),
                },
            },
            {
                "type": "input",
                "block_id": "risk",
                "optional": True,
                "label": {"type": "plain_text", "text": "Risk flags (optional, comma-separated)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": ", ".join([str(x) for x in spec.get("risk_flags", []) if str(x).strip()]),
                },
            },
            {
                "type": "input",
                "block_id": "links",
                "optional": True,
                "label": {"type": "plain_text", "text": "Links (one per line, optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": True,
                    "initial_value": "\n".join([str(x) for x in spec.get("links", []) if str(x).strip()]),
                },
            },
        ],
    }


def _feature_message_blocks(feature: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    fid = feature["id"]
    status = feature["status"]
    title = feature["title"]
    spec = feature.get("spec") or {}
    mode = spec.get("implementation_mode", "new_feature")
    preview = feature.get("preview_url") or ""
    pr = feature.get("github_pr_url") or ""
    issue = feature.get("github_issue_url") or ""
    validation = spec.get("_validation") or {}
    missing = validation.get("missing") or []
    missing_summary = ", ".join(missing) if missing else "none"

    actions: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open dashboard"},
            "url": f"{base_url}/features/{fid}",
        },
        {
            "type": "button",
            "action_id": "ff_add_details",
            "text": {"type": "plain_text", "text": "Add details"},
            "value": fid,
        },
        {
            "type": "button",
            "action_id": "ff_run_build",
            "text": {"type": "plain_text", "text": "Run build"},
            "style": "primary",
            "value": fid,
        },
        {
            "type": "button",
            "action_id": "ff_approve",
            "text": {"type": "plain_text", "text": "Approve"},
            "style": "danger",
            "value": fid,
        },
    ]

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{title}*\nStatus: `{status}`\nMode: `{mode}`\nID: `{fid}`\n"
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
                        f"Issue: {issue or '(none)'} | PR: {pr or '(pending)'} | "
                        f"Preview: {preview or '(none)'}"
                    ),
                }
            ],
        },
        {"type": "actions", "elements": actions},
    ]


def _validation_questions(feature: dict[str, Any]) -> list[str]:
    spec = feature.get("spec") or {}
    validation = spec.get("_validation") or {}
    missing = [str(x) for x in validation.get("missing") or []]
    questions: list[str] = []
    for field in missing:
        questions.append(QUESTION_BY_FIELD.get(field, f"Please provide `{field}`."))
    return questions


def _post_clarification_prompt(client: Any, channel_id: str, thread_ts: str, feature: dict[str, Any]) -> None:
    questions = _validation_questions(feature)
    if not questions:
        return

    prompt = "I need a few clarifications before I can start the build:\n"
    prompt += "\n".join([f"- {q}" for q in questions])
    prompt += "\nUse *Add details* to update the request."
    client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=prompt)


def _fetch_feature(settings: Any, feature_id: str) -> dict[str, Any]:
    headers = {}
    token = (settings.api_auth_token or "").strip()
    if token:
        headers["X-FF-Token"] = token
    r = httpx.get(
        f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}",
        timeout=30,
        headers=headers or None,
    )
    r.raise_for_status()
    return r.json()


def _update_feature_message(client: Any, feature: dict[str, Any], *, channel_id: str, message_ts: str) -> None:
    settings = get_settings()
    blocks = _feature_message_blocks(feature, settings.base_url)
    client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"Feature request: *{feature['title']}*",
        blocks=blocks,
    )


def main() -> None:
    settings = get_settings()

    if not settings.enable_slack_bot:
        console.print("[yellow]Slack bot is disabled (ENABLE_SLACK_BOT=false). Sleeping...[/yellow]")
        while True:
            time.sleep(3600)

    if not settings.slack_bot_token or not settings.slack_app_token:
        console.print("[red]Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN. Sleeping...[/red]")
        while True:
            time.sleep(3600)

    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret or "")

    @app.command("/feature")
    def handle_feature(ack, body, client, logger):
        ack()
        channel_id = body.get("channel_id")
        user_id = body.get("user_id")
        text = (body.get("text") or "").strip()

        allowed_channels = settings.slack_allowed_channel_set()
        allowed_users = settings.slack_allowed_user_set()

        if allowed_channels and channel_id not in allowed_channels:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Not allowed in this channel.")
            return

        if allowed_users and user_id not in allowed_users:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="You are not allowlisted.")
            return

        view = _build_feature_modal(text, channel_id)
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("ff_feature_submit")
    def handle_feature_submit(ack, body, view, client, logger):
        user_id = body["user"]["id"]
        meta = json.loads(view.get("private_metadata") or "{}")
        channel_id = meta.get("channel_id")

        values = view["state"]["values"]
        spec = _extract_spec_from_form(values)
        title = spec.get("title", "").strip() or "(untitled feature)"

        ack()

        msg = client.chat_postMessage(channel=channel_id, text=f"Feature request: *{title}* (creating...)")
        thread_ts = msg["ts"]

        payload = {
            "spec": spec,
            "requester_user_id": user_id,
            "slack_channel_id": channel_id,
            "slack_thread_ts": thread_ts,
            "slack_message_ts": thread_ts,
        }

        try:
            headers = {}
            token = (settings.api_auth_token or "").strip()
            if token:
                headers["X-FF-Token"] = token
            r = httpx.post(
                f"{settings.orchestrator_internal_url}/api/feature-requests",
                json=payload,
                timeout=30,
                headers=headers or None,
            )
            r.raise_for_status()
            feature = r.json()
        except Exception as e:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Failed to create request: `{e}`")
            return

        blocks = _feature_message_blocks(feature, settings.base_url)
        client.chat_update(channel=channel_id, ts=thread_ts, text=f"Feature request: *{title}*", blocks=blocks)

        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                f"Created request `{feature['id']}` with status `{feature['status']}`.\n"
                f"Mode: {_format_mode(str(spec.get('implementation_mode', 'new_feature')))}"
            ),
        )
        if feature.get("status") == "NEEDS_INFO":
            _post_clarification_prompt(client, channel_id, thread_ts, feature)

    @app.action("ff_add_details")
    def handle_add_details(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        channel_id = body.get("channel", {}).get("id")
        message_ts = body.get("message", {}).get("ts")
        thread_ts = body.get("message", {}).get("thread_ts") or message_ts

        try:
            feature = _fetch_feature(settings, feature_id)
        except Exception as e:
            user_id = body["user"]["id"]
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Could not load feature details: `{e}`",
            )
            return

        view = _build_update_modal(
            feature,
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts,
        )
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("ff_feature_update")
    def handle_feature_update(ack, body, view, client, logger):
        user_id = body["user"]["id"]
        meta = json.loads(view.get("private_metadata") or "{}")
        feature_id = meta.get("feature_id", "")
        channel_id = meta.get("channel_id", "")
        message_ts = meta.get("message_ts", "")
        thread_ts = meta.get("thread_ts", "") or message_ts

        values = view["state"]["values"]
        spec_patch = _extract_spec_from_form(values)

        ack()

        payload = {
            "spec": spec_patch,
            "actor_type": "slack",
            "actor_id": user_id,
            "message": "Spec updated from Slack Add details modal",
        }

        try:
            headers = {}
            token = (settings.api_auth_token or "").strip()
            if token:
                headers["X-FF-Token"] = token
            r = httpx.patch(
                f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/spec",
                json=payload,
                timeout=30,
                headers=headers or None,
            )
            r.raise_for_status()
            feature = r.json()
        except Exception as e:
            if channel_id and thread_ts:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"Could not save details for `{feature_id}`: `{e}`",
                )
            return

        if channel_id and message_ts:
            _update_feature_message(client, feature, channel_id=channel_id, message_ts=message_ts)

        if channel_id and thread_ts:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Updated request `{feature['id']}`. Status is now `{feature['status']}`.",
            )
            if feature.get("status") == "NEEDS_INFO":
                _post_clarification_prompt(client, channel_id, thread_ts, feature)
            elif feature.get("status") == "READY_FOR_BUILD":
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Spec looks complete. Click *Run build* when ready.",
                )

    @app.action("ff_run_build")
    def handle_run_build(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        user_id = body["user"]["id"]
        channel_id = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")

        try:
            current = _fetch_feature(settings, feature_id)
        except Exception:
            current = {}

        if current.get("status") == "NEEDS_INFO":
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="This request still needs details before build.",
            )
            _post_clarification_prompt(client, channel_id, thread_ts, current)
            return

        try:
            headers = {}
            token = (settings.api_auth_token or "").strip()
            if token:
                headers["X-FF-Token"] = token
            r = httpx.post(
                f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/build",
                json={"actor_type": "slack", "actor_id": user_id, "message": "Build requested from Slack"},
                timeout=30,
                headers=headers or None,
            )
            r.raise_for_status()
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text="Build enqueued")
        except Exception as e:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Failed to enqueue build: `{e}`")

    @app.action("ff_approve")
    def handle_approve(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        user_id = body["user"]["id"]
        channel_id = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")
        message_ts = body.get("message", {}).get("ts")

        if not is_approver_allowed(user_id):
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Only configured reviewers/admins can approve this feature.",
            )
            return

        try:
            headers = {}
            token = (settings.api_auth_token or "").strip()
            if token:
                headers["X-FF-Token"] = token
            r = httpx.post(
                f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/approve",
                params={"approver": user_id},
                timeout=30,
                headers=headers or None,
            )
            r.raise_for_status()
            feature = r.json()
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Approved by <@{user_id}>. Status now `{feature['status']}`",
            )

            if channel_id and message_ts:
                _update_feature_message(client, feature, channel_id=channel_id, message_ts=message_ts)
        except Exception as e:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Failed to approve: `{e}`")

    console.print("[green]Starting Slack Socket Mode handler...[/green]")
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
