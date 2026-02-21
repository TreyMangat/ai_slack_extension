from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from app.config import get_settings
from app.services.github_auth import get_github_token_provider
from app.services.github_adapter import GitHubAdapter
from app.services.llm_provider import LLMProvider, LLMProviderError
from app.services.prompt_optimizer import build_optimized_prompt


console = Console()


@dataclass
class CodeRunResult:
    github_pr_url: str = ""
    preview_url: str = ""


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
        fake_pr = f"https://example.local/github/pull/{issue_number}"
        fake_preview = f"http://localhost:8000/preview/{issue_number}"
        mode = (build_context or {}).get("implementation_mode", "new_feature")
        console.print(
            f"[bold cyan][MOCK CodeRunner][/bold cyan] kickoff issue #{issue_number} mode={mode} -> PR {fake_pr}"
        )
        return CodeRunResult(github_pr_url=fake_pr, preview_url=fake_preview)


class RealOpenCodeRunnerAdapter(CodeRunnerAdapter):
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
        # OpenCode is expected to be installed in the target repo.
        # We trigger it by posting a comment to the issue.
        mode = (build_context or {}).get("implementation_mode", "new_feature")
        repos = (build_context or {}).get("source_repos") or []
        context_lines = [
            "",
            "Build context:",
            f"- implementation_mode: {mode}",
            "- isolation_policy: work in isolated clone/copy only",
        ]
        if repos:
            context_lines.append("- source_repos:")
            context_lines.extend([f"  - {repo}" for repo in repos])

        await github.comment_issue(issue_number=issue_number, body=trigger_comment + "\n" + "\n".join(context_lines))
        console.print(f"[green]Triggered OpenCode via GitHub issue comment on #{issue_number}[/green]")
        # PR + preview will be populated later via webhooks in a real implementation.
        return CodeRunResult(github_pr_url="", preview_url="")


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


def _parse_repo_slug(repo_value: str) -> tuple[str, str]:
    text = (repo_value or "").strip()
    if not text:
        return "", ""

    if text.startswith("https://github.com/"):
        tail = text[len("https://github.com/") :]
        tail = tail.removesuffix(".git")
        parts = [p for p in tail.split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]
        return "", ""

    if "/" in text:
        parts = [p for p in text.removesuffix(".git").split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]
    return "", ""


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
        except Exception:
            continue
        rel_text = str(rel).replace("\\", "/")
        snippets.append(
            f"FILE: {rel_text}\n{content[:max_chars_per_file]}"
        )

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
) -> None:
    clone_url = f"https://github.com/{owner}/{repo}.git"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader=AUTHORIZATION: bearer {token}",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            clone_url,
            str(target_path),
        ],
        cwd=target_path.parent,
        timeout_seconds=600,
    )


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
        except Exception:
            pass


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

        repo_hint = str(final_spec.get("repo") or "").strip()
        owner, repo = _parse_repo_slug(repo_hint)
        if not owner or not repo:
            owner = (settings.github_repo_owner or "").strip()
            repo = (settings.github_repo_name or "").strip()
        if not owner or not repo:
            raise RuntimeError("Could not determine target repo (spec.repo or GITHUB_REPO_OWNER/NAME required)")

        token = self.token_provider.get_token()
        _prepare_target_repo(target_path=target_path, owner=owner, repo=repo, token=token)

        branch_prefix = (settings.llm_push_branch_prefix or "feature-factory").strip().strip("/")
        branch_name = f"{branch_prefix}/{issue_number}-{int(time.time())}"
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
                _run_shell(test_command, cwd=target_path, timeout_seconds=1800)
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
                f"http.https://github.com/.extraheader=AUTHORIZATION: bearer {token}",
                "push",
                "-u",
                "origin",
                branch_name,
            ],
            cwd=target_path,
            timeout_seconds=600,
        )

        pr_title = str(final_spec.get("title") or "").strip() or f"Feature request #{issue_number}"
        pr_body = (
            "Automated implementation generated by native LLM runner.\n\n"
            f"Feature request id: {feature_id or '(unknown)'}\n"
            f"Issue: #{issue_number}\n"
        )
        pr_url = await github.create_pull_request(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=(settings.github_default_branch or "main"),
        )
        console.print(f"[green]Native LLM runner opened PR: {pr_url}[/green]")
        return CodeRunResult(github_pr_url=pr_url, preview_url="")


def get_coderunner_adapter() -> CodeRunnerAdapter:
    settings = get_settings()
    if settings.mock_mode:
        return MockCodeRunnerAdapter()
    if settings.coderunner_mode_normalized() == "native_llm":
        return NativeLLMCodeRunnerAdapter()
    return RealOpenCodeRunnerAdapter()
