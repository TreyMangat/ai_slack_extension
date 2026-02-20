from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console

from app.config import get_settings
from app.services.github_adapter import GitHubAdapter


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


def get_coderunner_adapter() -> CodeRunnerAdapter:
    settings = get_settings()
    if settings.mock_mode:
        return MockCodeRunnerAdapter()
    return RealOpenCodeRunnerAdapter()
