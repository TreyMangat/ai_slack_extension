from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from app.config import Settings, get_settings
from app.services.github_auth import get_github_token_provider
from app.services.github_adapter import GitHubAdapter
from app.services.github_repo import resolve_repo_for_spec
from app.services.llm_provider import LLMProvider, LLMProviderError
from app.services.pr_description import build_standard_pr_body
from app.services.prompt_optimizer import build_optimized_prompt


console = Console()


@dataclass
class CodeRunResult:
    github_pr_url: str = ""
    preview_url: str = ""
    runner_metadata: dict[str, Any] = field(default_factory=dict)


class CodeRunnerAdapter:
    async def kickoff(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        trigger_comment: str,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
        feature_id: str = "",
    ) -> CodeRunResult:
        raise NotImplementedError


class MockCodeRunnerAdapter(CodeRunnerAdapter):
    async def kickoff(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        trigger_comment: str,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
        feature_id: str = "",
    ) -> CodeRunResult:
        # Pretend a PR was opened and a preview deployed.
        tracking_ref = _tracking_reference(issue_number=issue_number, feature_id=feature_id)
        fake_pr = f"https://example.local/github/pull/{tracking_ref}"
        fake_preview = f"http://localhost:8000/preview/{tracking_ref}"
        mode = (build_context or {}).get("implementation_mode", "new_feature")
        console.print(
            f"[bold cyan][MOCK CodeRunner][/bold cyan] kickoff {tracking_ref} mode={mode} -> PR {fake_pr}"
        )
        return CodeRunResult(
            github_pr_url=fake_pr,
            preview_url=fake_preview,
            runner_metadata={"runner": "mock"},
        )


def _truncate(value: str, *, max_chars: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 600,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(err)
    return (result.stdout or "").strip()


def _run_openclaw_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 1800,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    output = "\n".join([x for x in [result.stdout.strip(), result.stderr.strip()] if x]).strip()
    if result.returncode != 0:
        raise RuntimeError(output or "openclaw command failed")
    return output


def _run_shell(command: str, *, cwd: Path, timeout_seconds: int = 600, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "shell command failed"
        raise RuntimeError(err)
    return (result.stdout or "").strip()


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    maybe_json = text[start : end + 1]
    try:
        data = json.loads(maybe_json)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _tracking_reference(*, issue_number: int, feature_id: str) -> str:
    if issue_number > 0:
        return f"issue-{issue_number}"
    suffix = (feature_id or "").strip()
    if suffix:
        return f"feature-{suffix}"
    return f"request-{int(time.time())}"


def _branch_subject(*, issue_number: int, feature_id: str) -> str:
    if issue_number > 0:
        return str(issue_number)
    clean_feature = re.sub(r"[^a-zA-Z0-9_-]+", "-", (feature_id or "").strip()).strip("-")
    if clean_feature:
        return clean_feature[:36]
    return str(int(time.time()))


def _resolve_pr_base_branch(*, spec: dict[str, Any], settings: Settings) -> str:
    override = str((spec or {}).get("base_branch") or "").strip()
    if override:
        return override
    # Empty means "use repository default branch", resolved during clone.
    return ""


def _autogenerated_branch_prefixes(settings: Settings) -> tuple[str, ...]:
    prefixes: list[str] = ["prfactory/"]
    configured = (settings.llm_push_branch_prefix or "").strip().strip("/").lower()
    if configured:
        prefixes.append(f"{configured}/")
    unique: list[str] = []
    for item in prefixes:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _is_autogenerated_branch(branch_name: str, *, settings: Settings) -> bool:
    normalized = str(branch_name or "").strip().lower()
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in _autogenerated_branch_prefixes(settings))


def _collect_repo_context(repo_path: Path, *, max_files: int, max_chars_per_file: int) -> str:
    files: list[Path] = []
    ignore_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for filename in filenames:
            full = Path(root) / filename
            rel = full.relative_to(repo_path)
            files.append(rel)
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break

    file_list = sorted([str(p).replace("\\", "/") for p in files])
    selected: list[Path] = []
    priority = [
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
        "setup.py",
        ".github/workflows",
    ]
    for item in files:
        text = str(item).replace("\\", "/")
        if any(text == p or text.startswith(f"{p}/") for p in priority):
            selected.append(item)
    for item in files:
        if item in selected:
            continue
        if item.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".yml", ".yaml", ".md"}:
            selected.append(item)
        if len(selected) >= 25:
            break

    snippets: list[str] = []
    for rel in selected[:25]:
        path = repo_path / rel
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        rel_text = str(rel).replace("\\", "/")
        snippets.append(f"FILE: {rel_text}\n{content[:max_chars_per_file]}")

    return (
        "Repository files (truncated):\n"
        + "\n".join([f"- {p}" for p in file_list])
        + "\n\nRepresentative file contents:\n"
        + "\n\n".join(snippets)
    )


def _prepare_target_repo(
    *,
    target_path: Path,
    owner: str,
    repo: str,
    token: str,
    base_branch: str = "",
    settings: Settings | None = None,
) -> str:
    clone_url = f"https://github.com/{owner}/{repo}.git"
    auth_header = _github_basic_auth_header(token)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    clone_cmd = [
        "git",
        "-c",
        f"http.https://github.com/.extraheader={auth_header}",
        "clone",
        "--depth",
        "1",
        "--single-branch",
    ]
    normalized_base = (base_branch or "").strip()
    if normalized_base:
        clone_cmd.extend(["--branch", normalized_base])
    clone_cmd.extend([clone_url, str(target_path)])
    try:
        _run_command(
            clone_cmd,
            cwd=target_path.parent,
            timeout_seconds=600,
        )
    except Exception as e:  # noqa: BLE001
        message = str(e)
        lowered = message.lower()
        if normalized_base and (
            ("remote branch" in lowered and "not found" in lowered)
            or "couldn't find remote ref" in lowered
            or "could not find remote branch" in lowered
        ):
            raise RuntimeError(
                f"Configured base branch `{normalized_base}` does not exist in `{owner}/{repo}`. "
                "Choose an existing branch from the dropdown or reply `skip` to use the default branch."
            ) from e
        raise
    if normalized_base:
        return normalized_base
    resolved_default = ""
    try:
        checked_out = _run_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=target_path,
            timeout_seconds=30,
        ).strip()
        if checked_out and checked_out != "HEAD":
            resolved_default = checked_out
    except Exception:
        resolved_default = ""
    if not resolved_default:
        try:
            remote_head = _run_command(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=target_path,
                timeout_seconds=30,
            ).strip()
            marker = "refs/remotes/origin/"
            if remote_head.startswith(marker):
                resolved = remote_head[len(marker) :].strip()
                if resolved:
                    resolved_default = resolved
        except Exception:
            resolved_default = ""
    if settings is not None and resolved_default and _is_autogenerated_branch(resolved_default, settings=settings):
        raise RuntimeError(
            f"Repository default branch resolved to `{resolved_default}` in `{owner}/{repo}`, "
            "which looks like an auto-generated PRFactory branch. "
            "Set a stable default branch in GitHub (for example `main` or `develop`) "
            "or choose an explicit base branch in Slack."
        )
    return resolved_default


def _github_basic_auth_header(token: str) -> str:
    raw = f"x-access-token:{token}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"AUTHORIZATION: basic {encoded}"


def _apply_patch(repo_path: Path, patch_text: str) -> None:
    cleaned = (patch_text or "").strip()
    if not cleaned:
        raise RuntimeError("empty patch")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False, encoding="utf-8") as tmp:
        tmp.write(cleaned)
        tmp_path = Path(tmp.name)
    try:
        _run_command(["git", "apply", "--index", str(tmp_path)], cwd=repo_path, timeout_seconds=120)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _sanitize_agent_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw or "").strip()).strip("-")
    if not cleaned:
        cleaned = f"ff-{int(time.time())}"
    return cleaned[:80]


def _write_debug_codegen_file(
    *,
    repo_path: Path,
    feature_id: str,
    tracking_reference: str,
    spec: dict[str, Any],
) -> str:
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    title = str(spec.get("title") or "").strip() or "(untitled feature)"
    lines = [
        "# DEBUG_CODEGEN",
        "",
        "Generated by PRFactory debug build mode.",
        "",
        f"- generated_at_utc: {timestamp}",
        f"- feature_id: {feature_id or '(unknown)'}",
        f"- tracking_reference: {tracking_reference}",
        f"- title: {title}",
    ]
    target = repo_path / "DEBUG_CODEGEN.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return timestamp


def _build_local_openclaw_prompt(
    *,
    tracking_reference: str,
    feature_id: str,
    spec: dict[str, Any],
    build_context: dict[str, Any] | None,
    test_command: str,
) -> str:
    optimized_prompt = str(spec.get("optimized_prompt") or "").strip()
    if not optimized_prompt:
        optimized_prompt = build_optimized_prompt(spec)
    workspace_snapshot = (build_context or {}).get("workspace_snapshot") or {}
    prepared_references = workspace_snapshot.get("prepared_references") or []
    refs: list[str] = []
    for item in prepared_references:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        destination = str(item.get("destination") or "").strip()
        status = str(item.get("status") or "").strip()
        if source:
            refs.append(f"- {source} ({status}) -> {destination or '(n/a)'}")
    refs_block = "\n".join(refs) if refs else "- none"
    test_line = test_command if test_command else "(no explicit test command configured)"

    return (
        "You are running as an autonomous coding agent inside the target repository workspace.\n"
        "Make direct file edits and execute commands to implement the request safely.\n\n"
        f"Feature request id: {feature_id or '(unknown)'}\n"
        f"Tracking reference: {tracking_reference}\n\n"
        "Structured request:\n"
        f"{optimized_prompt}\n\n"
        "Reference snapshots (read-only context):\n"
        f"{refs_block}\n\n"
        "Execution requirements:\n"
        "- Keep changes scoped to the request and acceptance criteria.\n"
        "- You must produce concrete repository file edits (no no-op output).\n"
        "- Add or update tests for behavior changes.\n"
        "- Run relevant lint/test commands before finishing.\n"
        "- Do not push to remote and do not open PR yourself.\n"
        f"- Verification command expected by orchestrator: {test_line}\n\n"
        "Reply with concise JSON only:\n"
        '{"summary":"what changed","tests":"what you ran + results","risks":"remaining risks","commit_message":"optional commit message"}'
    )


def _looks_like_openclaw_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        'no api key found for provider "openai-codex"' in lowered
        or "requires an interactive tty" in lowered
        or "provider auth is not configured" in lowered
        or "failed to load auth" in lowered
    )


class RealOpenCodeRunnerAdapter(CodeRunnerAdapter):
    def __init__(self) -> None:
        self.settings = get_settings()
        self.token_provider = get_github_token_provider()

    async def kickoff(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        trigger_comment: str,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
        feature_id: str = "",
    ) -> CodeRunResult:
        mode = self.settings.opencode_execution_mode_normalized()
        if mode in {"", "local_openclaw"}:
            return await self._kickoff_local_openclaw(
                github=github,
                issue_number=issue_number,
                build_context=build_context,
                spec=spec,
                feature_id=feature_id,
            )
        raise RuntimeError(
            "Unsupported OPENCODE_EXECUTION_MODE. "
            "Issue-comment delegated mode was removed; use OPENCODE_EXECUTION_MODE=local_openclaw."
        )

    async def _kickoff_local_openclaw(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
        feature_id: str = "",
    ) -> CodeRunResult:
        settings = self.settings
        final_spec = spec or {}
        workspace_snapshot = (build_context or {}).get("workspace_snapshot") or {}
        target_path_raw = str(workspace_snapshot.get("target_path") or "").strip()
        if not target_path_raw:
            raise RuntimeError("local_openclaw mode requires workspace_snapshot.target_path")

        target_path = Path(target_path_raw).resolve()
        if target_path.exists() and any(target_path.iterdir()):
            raise RuntimeError(f"target workspace path is not empty: {target_path}")

        owner, repo = resolve_repo_for_spec(spec=final_spec, settings=settings)
        if not owner or not repo:
            raise RuntimeError("Could not determine target repo (spec.repo or GITHUB_REPO_OWNER/NAME required)")
        requested_pr_base = _resolve_pr_base_branch(spec=final_spec, settings=settings)

        github_actor_id = str((build_context or {}).get("github_actor_id") or "").strip()
        github_team_id = str((build_context or {}).get("slack_team_id") or "").strip()
        token = self.token_provider.get_token(
            owner=owner,
            repo=repo,
            actor_id=github_actor_id,
            team_id=github_team_id,
        )
        resolved_clone_base = _prepare_target_repo(
            target_path=target_path,
            owner=owner,
            repo=repo,
            token=token,
            base_branch=requested_pr_base,
            settings=settings,
        )
        pr_base = requested_pr_base or resolved_clone_base or (settings.github_default_branch or "main").strip() or "main"
        if not str(final_spec.get("base_branch") or "").strip():
            final_spec["base_branch"] = pr_base

        tracking_ref = _tracking_reference(issue_number=issue_number, feature_id=feature_id)
        branch_prefix = (settings.llm_push_branch_prefix or "prfactory").strip().strip("/")
        branch_subject = _branch_subject(issue_number=issue_number, feature_id=feature_id)
        branch_name = f"{branch_prefix}/{branch_subject}-{int(time.time())}"
        _run_command(["git", "checkout", "-b", branch_name], cwd=target_path, timeout_seconds=30)

        if (settings.llm_install_command or "").strip():
            _run_shell(settings.llm_install_command, cwd=target_path, timeout_seconds=1200)

        auth_dir = Path((settings.openclaw_auth_dir or "").strip() or "/home/app/.openclaw")
        if not auth_dir.exists():
            raise RuntimeError(
                f"OpenClaw auth directory not found in container: {auth_dir}. "
                "Sync auth into ./secrets/openclaw and mount it to OPENCLAW_AUTH_DIR."
            )

        test_command = (settings.llm_test_command or "").strip()
        prompt = _build_local_openclaw_prompt(
            tracking_reference=tracking_ref,
            feature_id=feature_id,
            spec=final_spec,
            build_context=build_context,
            test_command=test_command,
        )

        agent_suffix = feature_id[:8] if feature_id else branch_subject
        agent_id = _sanitize_agent_id(f"ff-{agent_suffix}-{int(time.time())}")
        cli_timeout = max(int(settings.opencode_timeout_seconds), 60)

        openclaw_reply = ""
        openclaw_meta: dict[str, Any] = {}
        openclaw_summary = ""
        openclaw_tests = ""
        openclaw_commit_message = ""
        debug_mode = bool(settings.opencode_debug_build) or bool(final_spec.get("debug_build"))
        debug_timestamp = ""

        if debug_mode:
            debug_timestamp = _write_debug_codegen_file(
                repo_path=target_path,
                feature_id=feature_id,
                tracking_reference=tracking_ref,
                spec=final_spec,
            )
            openclaw_summary = "Debug build mode wrote DEBUG_CODEGEN.md."
            openclaw_tests = "Debug mode skips model execution."
            openclaw_commit_message = "chore: add debug codegen marker"
            openclaw_meta = {"agentMeta": {"provider": "debug", "model": "debug-build"}}
        else:
            try:
                # Best-effort cleanup if an old temp id exists.
                try:
                    _run_openclaw_command(
                        ["openclaw", "agents", "delete", agent_id, "--force", "--json"],
                        cwd=target_path,
                        timeout_seconds=60,
                    )
                except Exception:  # noqa: BLE001
                    pass

                _run_openclaw_command(
                    [
                        "openclaw",
                        "agents",
                        "add",
                        agent_id,
                        "--non-interactive",
                        "--workspace",
                        str(target_path),
                        "--model",
                        settings.opencode_model,
                        "--json",
                    ],
                    cwd=target_path,
                    timeout_seconds=120,
                )

                raw = _run_openclaw_command(
                    [
                        "openclaw",
                        "agent",
                        "--local",
                        "--agent",
                        agent_id,
                        "--message",
                        prompt,
                        "--timeout",
                        str(cli_timeout),
                        "--json",
                    ],
                    cwd=target_path,
                    timeout_seconds=cli_timeout + 120,
                )
                payload = _extract_json_object(raw)
                payloads = payload.get("payloads") or []
                parts: list[str] = []
                for item in payloads:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                openclaw_reply = "\n".join(parts).strip()
                openclaw_meta = payload.get("meta") or {}

                reply_obj = _extract_json_object(openclaw_reply)
                if reply_obj:
                    openclaw_summary = str(reply_obj.get("summary") or "").strip()
                    openclaw_tests = str(reply_obj.get("tests") or "").strip()
                    openclaw_commit_message = str(reply_obj.get("commit_message") or "").strip()
                if not openclaw_summary and openclaw_reply:
                    openclaw_summary = _truncate(openclaw_reply, max_chars=1000)
            except Exception as e:  # noqa: BLE001
                text = str(e)
                if _looks_like_openclaw_auth_error(text):
                    raise RuntimeError(
                        "OpenClaw auth is unavailable in the worker container. "
                        f"Expected auth under {auth_dir}. "
                        "Run scripts/sync_openclaw_auth.ps1, then restart docker compose."
                    ) from e
                raise RuntimeError(f"OpenClaw local execution failed: {text}") from e
            finally:
                if not settings.opencode_keep_temp_agents:
                    try:
                        _run_openclaw_command(
                            ["openclaw", "agents", "delete", agent_id, "--force", "--json"],
                            cwd=target_path,
                            timeout_seconds=60,
                        )
                    except Exception:  # noqa: BLE001
                        pass

        verification_output = ""
        verification_warning = ""
        if test_command:
            try:
                verification_output = _run_shell(test_command, cwd=target_path, timeout_seconds=1800)
            except Exception as e:  # noqa: BLE001
                err_text = str(e)
                lowered = err_text.lower()
                missing_command = (
                    "not found" in lowered
                    or "no module named" in lowered
                    or "is not recognized as" in lowered
                )
                if missing_command:
                    verification_warning = (
                        "Verification command unavailable in runner environment: "
                        f"`{test_command}` -> {err_text}"
                    )
                else:
                    raise RuntimeError(
                        "Code was generated, but verification failed. "
                        f"Command `{test_command}` returned error: {e}"
                    ) from e

        status = _run_command(["git", "status", "--porcelain"], cwd=target_path, timeout_seconds=30)
        if not status.strip():
            raise RuntimeError(
                "OpenClaw run completed but produced no repository changes. "
                "Please add clearer acceptance criteria and try again."
            )

        _run_command(["git", "config", "user.name", settings.llm_commit_author_name], cwd=target_path, timeout_seconds=30)
        _run_command(
            ["git", "config", "user.email", settings.llm_commit_author_email],
            cwd=target_path,
            timeout_seconds=30,
        )
        _run_command(["git", "add", "-A"], cwd=target_path, timeout_seconds=60)

        commit_message = (openclaw_commit_message or "").strip()
        if not commit_message:
            title = str(final_spec.get("title") or "").strip()
            commit_message = f"feat: {title}" if title else "feat: implement request"
        commit_message = commit_message[:120]
        _run_command(["git", "commit", "-m", commit_message], cwd=target_path, timeout_seconds=60)

        _run_command(
            [
                "git",
                "-c",
                f"http.https://github.com/.extraheader={_github_basic_auth_header(token)}",
                "push",
                "-u",
                "origin",
                branch_name,
            ],
            cwd=target_path,
            timeout_seconds=600,
        )

        pr_title = str(final_spec.get("title") or "").strip() or f"Feature request {tracking_ref}"
        summary_lines: list[str] = []
        if openclaw_summary:
            summary_lines.append(openclaw_summary)
        summary_text = "\n".join(summary_lines).strip() or "Automated implementation completed."

        verification_text_parts = [openclaw_tests, verification_output]
        verification_text = "\n".join([x for x in verification_text_parts if str(x or "").strip()])
        pr_body = build_standard_pr_body(
            spec=final_spec,
            feature_id=feature_id,
            issue_number=issue_number if issue_number > 0 else None,
            branch_name=branch_name,
            runner_name="opencode-local-openclaw",
            runner_model=settings.opencode_model,
            summary=summary_text,
            verification_output=_truncate(verification_text or "(not reported)", max_chars=2000),
            verification_command=test_command,
            verification_warning=_truncate(verification_warning, max_chars=1200),
            preview_url="",
            cloudflare_project_name=settings.cloudflare_pages_project_name,
            cloudflare_production_branch=settings.cloudflare_pages_production_branch,
            repo_path=target_path,
        )
        pr_url = await github.create_pull_request(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=pr_base,
        )

        provider = str((openclaw_meta.get("agentMeta") or {}).get("provider") or "openai-codex")
        model = str((openclaw_meta.get("agentMeta") or {}).get("model") or settings.opencode_model)
        console.print(f"[green]Local OpenClaw runner opened PR: {pr_url}[/green]")
        return CodeRunResult(
            github_pr_url=pr_url,
            preview_url="",
            runner_metadata={
                "runner": "opencode",
                "execution_mode": "local_openclaw_debug" if debug_mode else "local_openclaw",
                "debug_build": bool(debug_mode),
                "debug_timestamp": debug_timestamp,
                "provider": provider,
                "model": model,
                "branch_name": branch_name,
                "tracking_reference": tracking_ref,
                "verification_command": test_command,
                "verification_output": _truncate(verification_output, max_chars=2000),
                "verification_warning": _truncate(verification_warning, max_chars=1200),
                "assistant_summary": _truncate(openclaw_summary, max_chars=2000),
                "assistant_reply": _truncate(openclaw_reply, max_chars=3000),
            },
        )


class NativeLLMCodeRunnerAdapter(CodeRunnerAdapter):
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = LLMProvider(self.settings)
        self.token_provider = get_github_token_provider()

    async def kickoff(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        trigger_comment: str,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
        feature_id: str = "",
    ) -> CodeRunResult:
        settings = self.settings
        final_spec = spec or {}
        workspace_snapshot = (build_context or {}).get("workspace_snapshot") or {}
        target_path_raw = str(workspace_snapshot.get("target_path") or "").strip()
        if not target_path_raw:
            raise RuntimeError("native_llm mode requires workspace_snapshot.target_path")

        target_path = Path(target_path_raw).resolve()
        if target_path.exists() and any(target_path.iterdir()):
            raise RuntimeError(f"target workspace path is not empty: {target_path}")

        owner, repo = resolve_repo_for_spec(spec=final_spec, settings=settings)
        if not owner or not repo:
            raise RuntimeError("Could not determine target repo (spec.repo or GITHUB_REPO_OWNER/NAME required)")
        requested_pr_base = _resolve_pr_base_branch(spec=final_spec, settings=settings)

        github_actor_id = str((build_context or {}).get("github_actor_id") or "").strip()
        github_team_id = str((build_context or {}).get("slack_team_id") or "").strip()
        token = self.token_provider.get_token(
            owner=owner,
            repo=repo,
            actor_id=github_actor_id,
            team_id=github_team_id,
        )
        resolved_clone_base = _prepare_target_repo(
            target_path=target_path,
            owner=owner,
            repo=repo,
            token=token,
            base_branch=requested_pr_base,
            settings=settings,
        )
        pr_base = requested_pr_base or resolved_clone_base or (settings.github_default_branch or "main").strip() or "main"
        if not str(final_spec.get("base_branch") or "").strip():
            final_spec["base_branch"] = pr_base

        tracking_ref = _tracking_reference(issue_number=issue_number, feature_id=feature_id)
        branch_prefix = (settings.llm_push_branch_prefix or "prfactory").strip().strip("/")
        branch_subject = _branch_subject(issue_number=issue_number, feature_id=feature_id)
        branch_name = f"{branch_prefix}/{branch_subject}-{int(time.time())}"
        _run_command(["git", "checkout", "-b", branch_name], cwd=target_path, timeout_seconds=30)

        optimized_prompt = str(final_spec.get("optimized_prompt") or "").strip()
        if not optimized_prompt:
            optimized_prompt = build_optimized_prompt(final_spec)

        if (settings.llm_install_command or "").strip():
            _run_shell(settings.llm_install_command, cwd=target_path, timeout_seconds=1200)

        repo_context = _collect_repo_context(
            target_path,
            max_files=max(settings.llm_repo_max_files, 50),
            max_chars_per_file=max(settings.llm_repo_file_max_chars, 1000),
        )

        patch_result = None
        previous_failure = ""
        attempts = max(settings.llm_max_patch_rounds, 1)
        test_command = (settings.llm_test_command or "").strip()
        verification_output = ""

        for attempt in range(1, attempts + 1):
            try:
                patch_result = self.provider.request_code_patch(
                    optimized_prompt=optimized_prompt,
                    repository_context=repo_context,
                    previous_failure=previous_failure,
                )
            except LLMProviderError as e:
                raise RuntimeError(f"LLM patch generation failed: {e}") from e

            _apply_patch(target_path, patch_result.patch)

            if not test_command:
                break
            try:
                verification_output = _run_shell(test_command, cwd=target_path, timeout_seconds=1800)
                break
            except Exception as e:  # noqa: BLE001
                previous_failure = f"Attempt {attempt} failed tests:\n{e}"
                if attempt == attempts:
                    raise RuntimeError(previous_failure) from e

        status = _run_command(["git", "status", "--porcelain"], cwd=target_path, timeout_seconds=30)
        if not status.strip():
            raise RuntimeError("LLM run produced no changes")

        _run_command(["git", "config", "user.name", settings.llm_commit_author_name], cwd=target_path, timeout_seconds=30)
        _run_command(
            ["git", "config", "user.email", settings.llm_commit_author_email],
            cwd=target_path,
            timeout_seconds=30,
        )
        _run_command(["git", "add", "-A"], cwd=target_path, timeout_seconds=60)

        commit_message = "feat: implement request"
        if patch_result and patch_result.commit_message:
            commit_message = patch_result.commit_message.strip()[:120]
        _run_command(["git", "commit", "-m", commit_message], cwd=target_path, timeout_seconds=60)

        _run_command(
            [
                "git",
                "-c",
                f"http.https://github.com/.extraheader={_github_basic_auth_header(token)}",
                "push",
                "-u",
                "origin",
                branch_name,
            ],
            cwd=target_path,
            timeout_seconds=600,
        )

        pr_title = str(final_spec.get("title") or "").strip() or f"Feature request {tracking_ref}"
        summary_lines: list[str] = []
        if patch_result and patch_result.rationale:
            summary_lines.append(str(patch_result.rationale))
        summary_text = "\n".join([x for x in summary_lines if str(x or "").strip()]).strip()
        if not summary_text:
            summary_text = "Automated implementation generated by native LLM runner."
        verification_text = "\n".join([x for x in [verification_output] if str(x or "").strip()])
        pr_body = build_standard_pr_body(
            spec=final_spec,
            feature_id=feature_id,
            issue_number=issue_number if issue_number > 0 else None,
            branch_name=branch_name,
            runner_name="native-llm",
            runner_model=settings.llm_model,
            summary=summary_text,
            verification_output=_truncate(verification_text or "(not reported)", max_chars=2000),
            verification_command=test_command,
            verification_warning="",
            preview_url="",
            cloudflare_project_name=settings.cloudflare_pages_project_name,
            cloudflare_production_branch=settings.cloudflare_pages_production_branch,
            repo_path=target_path,
        )
        pr_url = await github.create_pull_request(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=pr_base,
        )
        console.print(f"[green]Native LLM runner opened PR: {pr_url}[/green]")
        return CodeRunResult(
            github_pr_url=pr_url,
            preview_url="",
            runner_metadata={
                "runner": "native_llm",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "branch_name": branch_name,
                "attempts": attempts,
                "test_command": test_command,
            },
        )


def get_coderunner_adapter() -> CodeRunnerAdapter:
    settings = get_settings()
    if settings.mock_mode:
        return MockCodeRunnerAdapter()
    if settings.coderunner_mode_normalized() == "native_llm":
        return NativeLLMCodeRunnerAdapter()
    return RealOpenCodeRunnerAdapter()
