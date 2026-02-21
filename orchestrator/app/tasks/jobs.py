from __future__ import annotations

import json
from textwrap import dedent
from typing import Any

from rich.console import Console

from app.config import get_settings
from app.db import db_session
from app.models import FeatureRequest
from app.observability import metrics
from app.services.coderunner_adapter import get_coderunner_adapter
from app.services.event_logger import log_event
from app.services.github_adapter import get_github_adapter
from app.services.reviewer_service import notify_reviewer_for_approval
from app.services.slack_adapter import get_slack_adapter
from app.services.url_safety import normalize_external_url
from app.services.workspace_service import (
    WorkspacePreparationResult,
    prepare_workspace,
)
from app.state_machine import BUILDING, perform_action, validate_transition


console = Console()


def _workspace_plan(
    spec: dict[str, Any],
    feature_id: str,
    workspace: WorkspacePreparationResult | None = None,
) -> dict[str, Any]:
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    source_repos = [str(x).strip() for x in (spec.get("source_repos") or []) if str(x).strip()]
    plan: dict[str, Any] = {
        "workspace_id": f"ff-{feature_id}",
        "implementation_mode": mode,
        "source_repos": source_repos,
        "isolation_policy": "work in isolated clone/copy; never push directly to source repos",
        "recommended_steps": [
            "clone referenced repository into temporary workspace",
            "create a dedicated feature branch",
            "implement and test changes",
            "open PR for reviewer/admin approval",
        ],
    }
    if workspace:
        plan["workspace_snapshot"] = {
            "workspace_path": workspace.workspace_path,
            "target_path": workspace.target_path,
            "manifest_path": workspace.manifest_path,
            "prepared_references": [
                {
                    "source": r.source,
                    "destination": r.destination,
                    "method": r.method,
                    "status": r.status,
                    "detail": r.detail,
                }
                for r in workspace.prepared_references
            ],
            "errors": workspace.errors,
        }
    return plan


def _issue_body_from_feature(feature: FeatureRequest, workspace_plan: dict[str, Any] | None = None) -> str:
    spec = feature.spec or {}
    ac = spec.get("acceptance_criteria") or []
    non_goals = spec.get("non_goals") or []
    source_repos = spec.get("source_repos") or []

    ac_bullets = "\n".join([f"- {x}" for x in ac]) if ac else "- (none provided)"
    ng_bullets = "\n".join([f"- {x}" for x in non_goals]) if non_goals else "- (none)"
    risk = ", ".join(spec.get("risk_flags") or []) or "(none)"
    source_repo_bullets = "\n".join([f"- {x}" for x in source_repos]) if source_repos else "- (none)"
    final_workspace_plan = workspace_plan or _workspace_plan(spec, feature.id)
    optimized_prompt = str(spec.get("optimized_prompt", "")).strip()
    optimized_prompt_section = ""
    if optimized_prompt:
        optimized_prompt_section = (
            "\n## Optimized Build Prompt\n"
            "```text\n"
            f"{optimized_prompt}\n"
            "```\n"
        )

    return dedent(
        f"""
        ## Feature Spec

        **Title:** {spec.get('title', '')}

        **Problem:**
        {spec.get('problem', '')}

        **Business justification (why now):**
        {spec.get('business_justification', '')}

        **Proposed solution:**
        {spec.get('proposed_solution', '')}

        **Implementation mode:** {spec.get('implementation_mode', 'new_feature')}

        **Source repos (reference-only):**
        {source_repo_bullets}

        **Acceptance criteria:**
        {ac_bullets}

        **Non-goals:**
        {ng_bullets}

        **Risk flags:** {risk}

        ## Safe Workspace Plan
        ```json
        {json.dumps(final_workspace_plan, indent=2)}
        ```
        {optimized_prompt_section}

        ---
        ### Raw spec JSON
        ```json
        {json.dumps(spec, indent=2)}
        ```
        """
    ).strip()


async def kickoff_build(feature_id: str) -> None:
    """Background job: create issue, trigger code runner, store PR/preview."""

    settings = get_settings()
    github = get_github_adapter()
    coderunner = get_coderunner_adapter()
    slack = get_slack_adapter()

    with db_session() as db:
        feature = db.get(FeatureRequest, feature_id)
        if not feature:
            console.print(f"[red]Feature {feature_id} not found[/red]")
            return

        metrics.inc("build_jobs_started_total", 1)

        # API/UI should already transition to BUILDING before enqueue.
        # Keep this fallback for compatibility with older queued jobs.
        if feature.status != BUILDING:
            action_result = perform_action(feature.status, "start_build")
            validate_transition(feature.status, action_result.new_status)
            feature.status = action_result.new_status

        log_event(db, feature, event_type="build_started", message="Build started")

        spec = feature.spec or {}

        workspace_result = prepare_workspace(feature.id, spec)
        log_event(
            db,
            feature,
            event_type="workspace_prepared",
            message="Prepared isolated workspace snapshot",
            data=workspace_result.to_event_data(),
        )

        workspace_plan = _workspace_plan(spec, feature.id, workspace=workspace_result)
        log_event(
            db,
            feature,
            event_type="workspace_plan_prepared",
            message="Prepared isolated workspace plan",
            data=workspace_plan,
        )

        mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
        prepared_count = len([r for r in workspace_result.prepared_references if r.status == "prepared"])
        source_repo_count = len([r for r in workspace_result.source_repos if r.strip()])
        if mode == "reuse_existing" and source_repo_count > 0 and prepared_count == 0:
            message = (
                "Reuse mode requested, but no source repository snapshots were prepared. "
                "Provide a local source path under WORKSPACE_LOCAL_COPY_ROOT or enable git cloning."
            )
            feature.last_error = message
            action_result = perform_action(feature.status, "fail_build")
            validate_transition(feature.status, action_result.new_status)
            feature.status = action_result.new_status
            feature.active_build_job_id = ""
            log_event(db, feature, event_type="build_failed", message=message)
            log_event(
                db,
                feature,
                event_type="dead_letter_external_call",
                message="Workspace preparation failed before external execution",
                data={"stage": "workspace_prepare", "error": message},
            )
            metrics.inc("build_jobs_failed_total", 1)
            if feature.slack_channel_id and feature.slack_thread_ts:
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=f"Build failed for *{feature.title}*: `{message}`",
                )
            return

        if feature.slack_channel_id and feature.slack_thread_ts:
            slack.post_thread_message(
                channel=feature.slack_channel_id,
                thread_ts=feature.slack_thread_ts,
                text=(
                    f"Started build for *{feature.title}* (id: `{feature.id}`) | mode: `{mode}` | "
                    f"prepared references: {prepared_count}/{source_repo_count}"
                ),
            )

        stage = "github_issue_create"
        try:
            issue = await github.create_issue(
                title=feature.title,
                body=_issue_body_from_feature(feature, workspace_plan=workspace_plan),
                labels=["feature-factory"],
            )
            safe_issue_url = normalize_external_url(issue.html_url)
            if safe_issue_url:
                feature.github_issue_url = safe_issue_url
            log_event(
                db,
                feature,
                event_type="github_issue_created",
                message=f"Created GitHub issue #{issue.number}",
                data={"issue_url": safe_issue_url, "issue_number": issue.number},
            )

            stage = "coderunner_kickoff"
            result = await coderunner.kickoff(
                github=github,
                issue_number=issue.number,
                trigger_comment=settings.opencode_trigger_comment,
                build_context=workspace_plan,
                spec=spec,
                feature_id=feature.id,
            )

            action_result = perform_action(feature.status, "opened_pr")
            validate_transition(feature.status, action_result.new_status)
            feature.status = action_result.new_status

            safe_pr_url = normalize_external_url(result.github_pr_url or "")
            if safe_pr_url:
                feature.github_pr_url = safe_pr_url
            log_event(
                db,
                feature,
                event_type="pr_opened",
                message="PR opened (or trigger sent)",
                data={"pr_url": safe_pr_url},
            )

            if feature.slack_channel_id and feature.slack_thread_ts:
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=(
                        f"PR step complete for *{feature.title}*\n"
                        f"Issue: {feature.github_issue_url or '(none)'}\n"
                        f"PR: {feature.github_pr_url or '(pending from OpenCode)'}"
                    ),
                )

            safe_preview_url = normalize_external_url(result.preview_url or "")
            if safe_preview_url:
                feature.preview_url = safe_preview_url
                action_result = perform_action(feature.status, "preview_ready")
                validate_transition(feature.status, action_result.new_status)
                feature.status = action_result.new_status
                feature.active_build_job_id = ""

                log_event(
                    db,
                    feature,
                    event_type="preview_ready",
                    message="Preview ready",
                    data={"preview_url": safe_preview_url},
                )

                if feature.slack_channel_id and feature.slack_thread_ts:
                    slack.post_thread_message(
                        channel=feature.slack_channel_id,
                        thread_ts=feature.slack_thread_ts,
                        text=f"Preview ready: {safe_preview_url}",
                    )

                if notify_reviewer_for_approval(feature, slack):
                    log_event(
                        db,
                        feature,
                        event_type="reviewer_notified",
                        message="Reviewer/admin notified for approval",
                    )
            else:
                # Preview URL will be set later via signed integration callback.
                feature.active_build_job_id = ""
            metrics.inc("build_jobs_succeeded_total", 1)

        except Exception as e:
            feature.last_error = str(e)
            try:
                action_result = perform_action(feature.status, "fail_build")
                validate_transition(feature.status, action_result.new_status)
                feature.status = action_result.new_status
            except Exception:
                pass
            feature.active_build_job_id = ""

            log_event(
                db,
                feature,
                event_type="build_failed",
                message=f"Build failed: {e}",
            )
            log_event(
                db,
                feature,
                event_type="dead_letter_external_call",
                message="External integration call exhausted retries",
                data={"stage": stage, "error": str(e)},
            )
            metrics.inc("build_jobs_failed_total", 1)

            if feature.slack_channel_id and feature.slack_thread_ts:
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=f"Build failed for *{feature.title}*: `{e}`",
                )


def kickoff_build_job(feature_id: str) -> None:
    """RQ entrypoint (sync)."""

    import asyncio

    asyncio.run(kickoff_build(feature_id))
