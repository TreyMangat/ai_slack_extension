from __future__ import annotations

from typing import Any

from rich.console import Console

from app.config import get_settings
from app.db import db_session
from app.models import FeatureRequest
from app.observability import metrics
from app.services.coderunner_adapter import get_coderunner_adapter
from app.services.event_logger import log_event
from app.services.github_adapter import get_github_adapter
from app.services.github_repo import resolve_repo_for_spec
from app.services.reviewer_service import notify_reviewer_for_approval
from app.services.slack_adapter import get_slack_adapter
from app.services.url_safety import normalize_external_url
from app.services.workspace_service import (
    WorkspacePreparationResult,
    prepare_workspace,
)
from app.state_machine import BUILDING, perform_action, validate_transition


console = Console()


def _truncate_for_event(value: Any, *, max_chars: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


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


async def kickoff_build(feature_id: str) -> None:
    """Background job: run code runner and store PR/preview outputs."""

    settings = get_settings()
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

        if not settings.mock_mode and not settings.github_enabled:
            message = "GitHub integration must be enabled for non-mock builds (set GITHUB_ENABLED=true)."
            feature.last_error = message
            action_result = perform_action(feature.status, "fail_build")
            validate_transition(feature.status, action_result.new_status)
            feature.status = action_result.new_status
            feature.active_build_job_id = ""
            log_event(db, feature, event_type="build_failed", message=message)
            metrics.inc("build_jobs_failed_total", 1)
            if feature.slack_channel_id and feature.slack_thread_ts:
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=f"Build failed for *{feature.title}*: `{message}`",
                )
            return

        owner, repo = resolve_repo_for_spec(spec=spec, settings=settings)
        target_repo = f"{owner}/{repo}" if owner and repo else ""
        feature.github_issue_url = ""
        if not settings.mock_mode and not target_repo:
            message = "No target repository configured. Set `spec.repo` (org/repo) or GITHUB_REPO_OWNER/GITHUB_REPO_NAME."
            feature.last_error = message
            action_result = perform_action(feature.status, "fail_build")
            validate_transition(feature.status, action_result.new_status)
            feature.status = action_result.new_status
            feature.active_build_job_id = ""
            log_event(db, feature, event_type="build_failed", message=message)
            metrics.inc("build_jobs_failed_total", 1)
            if feature.slack_channel_id and feature.slack_thread_ts:
                install_url = settings.github_app_install_url_resolved()
                install_hint = f"\nInstall URL: {install_url}" if install_url else ""
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=f"Build failed for *{feature.title}*: `{message}`{install_hint}",
                )
            return

        if feature.slack_channel_id and feature.slack_thread_ts:
            slack.post_thread_message(
                channel=feature.slack_channel_id,
                thread_ts=feature.slack_thread_ts,
                text=(
                    f"Started build for *{feature.title}* (id: `{feature.id}`) | mode: `{mode}` | "
                    f"prepared references: {prepared_count}/{source_repo_count}"
                    + (f" | repo: `{target_repo}`" if target_repo else "")
                ),
            )

        stage = "github_adapter_init"
        try:
            github = get_github_adapter(owner=owner, repo=repo, strict=(not settings.mock_mode and settings.github_enabled))
            if target_repo:
                log_event(
                    db,
                    feature,
                    event_type="github_repo_resolved",
                    message=f"Resolved target repo {target_repo}",
                    data={"repo": target_repo},
                )

            stage = "coderunner_kickoff"
            optimized_prompt = _truncate_for_event(spec.get("optimized_prompt", ""), max_chars=6000)
            log_event(
                db,
                feature,
                event_type="coderunner_invoked",
                message="Dispatching request to code runner",
                data={
                    "coderunner_mode": settings.coderunner_mode_normalized(),
                    "opencode_execution_mode": settings.opencode_execution_mode_normalized(),
                    "implementation_mode": mode,
                    "repo": spec.get("repo", ""),
                    "source_repos": spec.get("source_repos") or [],
                    "acceptance_criteria": spec.get("acceptance_criteria") or [],
                    "links": spec.get("links") or [],
                    "ui_feature": bool(spec.get("ui_feature")),
                    "ui_keywords": spec.get("ui_keywords") or [],
                    "optimized_prompt": optimized_prompt,
                    "target_repo": target_repo,
                },
            )

            result = await coderunner.kickoff(
                github=github,
                issue_number=0,
                trigger_comment="",
                build_context=workspace_plan,
                spec=spec,
                feature_id=feature.id,
            )
            runner_metadata = result.runner_metadata if isinstance(result.runner_metadata, dict) else {}
            log_event(
                db,
                feature,
                event_type="coderunner_completed",
                message="Code runner completed",
                data={
                    "github_pr_url": result.github_pr_url or "",
                    "preview_url": result.preview_url or "",
                    "runner_metadata": runner_metadata,
                },
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
                message="PR opened",
                data={"pr_url": safe_pr_url},
            )

            if feature.slack_channel_id and feature.slack_thread_ts:
                runner_line = ""
                if runner_metadata:
                    provider = str(runner_metadata.get("provider") or "").strip()
                    model = str(runner_metadata.get("model") or "").strip()
                    execution_mode = str(runner_metadata.get("execution_mode") or "").strip()
                    parts = [x for x in [execution_mode, provider, model] if x]
                    if parts:
                        runner_line = f"\nRunner: `{ ' | '.join(parts) }`"
                slack.post_thread_message(
                    channel=feature.slack_channel_id,
                    thread_ts=feature.slack_thread_ts,
                    text=(
                        f"PR step complete for *{feature.title}*\n"
                        f"Repo: {target_repo or '(none)'}\n"
                        f"PR: {feature.github_pr_url or '(pending)'}"
                        f"{runner_line}"
                    ),
                )
                if bool(spec.get("ui_feature")):
                    cloudflare_project = (settings.cloudflare_pages_project_name or "").strip() or "(configure CLOUDFLARE_PAGES_PROJECT_NAME)"
                    slack.post_thread_message(
                        channel=feature.slack_channel_id,
                        thread_ts=feature.slack_thread_ts,
                        text=(
                            "UI request detected. To view preview, open the PR Checks tab and click the "
                            f"Cloudflare Pages deployment. Project: `{cloudflare_project}`."
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
