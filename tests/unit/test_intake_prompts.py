"""Tests for the dynamic intake system prompt builder."""
from __future__ import annotations

from app.services.github_connection import GitHubConnectionCheck, GitHubConnectionStatus
from app.services.intake_prompts import build_intake_system_prompt


class TestPromptIncludesRepoCatalog:
    def test_prompt_includes_repo_catalog_when_available(self) -> None:
        repos = [
            {"full_name": "org/frontend", "description": "React web app"},
            {"full_name": "org/backend", "description": "FastAPI service"},
        ]
        prompt = build_intake_system_prompt(available_repos=repos)
        assert "AVAILABLE REPOS:" in prompt
        assert "org/frontend" in prompt
        assert "React web app" in prompt
        assert "org/backend" in prompt

    def test_prompt_excludes_repo_section_when_no_repos(self) -> None:
        prompt = build_intake_system_prompt(available_repos=None)
        assert "AVAILABLE REPOS:" not in prompt

    def test_prompt_excludes_repo_section_for_empty_list(self) -> None:
        prompt = build_intake_system_prompt(available_repos=[])
        assert "AVAILABLE REPOS:" not in prompt


class TestPromptIncludesBranchList:
    def test_prompt_includes_branch_list(self) -> None:
        branches = {
            "org/frontend": ["main", "develop", "feat/dark-mode"],
            "org/backend": ["main", "staging"],
        }
        prompt = build_intake_system_prompt(available_branches=branches)
        assert "AVAILABLE BRANCHES:" in prompt
        assert "org/frontend" in prompt
        assert "feat/dark-mode" in prompt
        assert "staging" in prompt

    def test_prompt_excludes_branches_when_none(self) -> None:
        prompt = build_intake_system_prompt(available_branches=None)
        assert "AVAILABLE BRANCHES:" not in prompt


class TestPromptIncludesUserHistory:
    def test_prompt_includes_user_history(self) -> None:
        history = [
            {"title": "Add dark mode", "repo": "org/frontend", "status": "MERGED"},
            {"title": "Fix login bug", "repo": "org/backend", "status": "PR_OPENED"},
        ]
        prompt = build_intake_system_prompt(user_history=history)
        assert "USER'S RECENT REQUESTS" in prompt
        assert "Add dark mode" in prompt
        assert "org/frontend" in prompt
        assert "MERGED" in prompt

    def test_prompt_excludes_history_when_none(self) -> None:
        prompt = build_intake_system_prompt(user_history=None)
        assert "USER'S RECENT REQUESTS" not in prompt

    def test_prompt_excludes_history_for_empty_list(self) -> None:
        prompt = build_intake_system_prompt(user_history=[])
        assert "USER'S RECENT REQUESTS" not in prompt


class TestPromptIncludesEscalationRules:
    def test_prompt_includes_escalation_rules(self) -> None:
        prompt = build_intake_system_prompt()
        assert "ESCALATION RULES:" in prompt
        assert "escalate" in prompt.lower()
        assert "multiple repos" in prompt.lower() or "architectural" in prompt.lower()


class TestPromptIncludesSkillDetection:
    def test_prompt_includes_skill_detection_instructions(self) -> None:
        prompt = build_intake_system_prompt()
        assert "SKILL DETECTION:" in prompt
        assert "developer" in prompt
        assert "non_technical" in prompt.lower() or "non-technical" in prompt.lower()
        assert "user_skill" in prompt


class TestPromptIncludesExamples:
    def test_prompt_includes_examples(self) -> None:
        prompt = build_intake_system_prompt()
        assert "EXAMPLES:" in prompt
        assert "CORS" in prompt
        assert "mobile" in prompt.lower()

    def test_prompt_includes_response_format(self) -> None:
        prompt = build_intake_system_prompt()
        assert "RESPONSE FORMAT:" in prompt
        assert "suggested_repo" in prompt
        assert "suggested_branch" in prompt


class TestPromptOrgConventions:
    def test_prompt_includes_org_conventions(self) -> None:
        conventions = {
            "branch_prefix": "feat/ for features, fix/ for bugs",
            "default_base": "develop",
        }
        prompt = build_intake_system_prompt(org_conventions=conventions)
        assert "ORG CONVENTIONS:" in prompt
        assert "branch_prefix" in prompt
        assert "feat/ for features" in prompt

    def test_prompt_excludes_conventions_when_none(self) -> None:
        prompt = build_intake_system_prompt(org_conventions=None)
        assert "ORG CONVENTIONS:" not in prompt


class TestPromptAssembly:
    def test_role_always_present(self) -> None:
        prompt = build_intake_system_prompt()
        assert "ROLE:" in prompt
        assert "PRFactory" in prompt

    def test_required_fields_always_present(self) -> None:
        prompt = build_intake_system_prompt()
        assert "REQUIRED FIELDS" in prompt
        assert "title:" in prompt
        assert "acceptance_criteria:" in prompt

    def test_full_context_prompt_includes_all_sections(self) -> None:
        repos = [{"full_name": "org/app", "description": "Main app"}]
        branches = {"org/app": ["main", "develop"]}
        history = [{"title": "Past request", "repo": "org/app", "status": "MERGED"}]
        conventions = {"test_command": "pytest"}

        prompt = build_intake_system_prompt(
            available_repos=repos,
            available_branches=branches,
            user_history=history,
            org_conventions=conventions,
        )
        assert "ROLE:" in prompt
        assert "REQUIRED FIELDS" in prompt
        assert "SKILL DETECTION:" in prompt
        assert "AVAILABLE REPOS:" in prompt
        assert "AVAILABLE BRANCHES:" in prompt
        assert "USER'S RECENT REQUESTS" in prompt
        assert "ORG CONVENTIONS:" in prompt
        assert "ESCALATION RULES:" in prompt
        assert "RESPONSE FORMAT:" in prompt
        assert "EXAMPLES:" in prompt

    def test_repo_hint_in_required_fields_when_repos_available(self) -> None:
        repos = [{"full_name": "org/app"}]
        prompt = build_intake_system_prompt(available_repos=repos)
        assert "Suggest from the repo catalog" in prompt


class TestPromptGitHubStatus:
    def test_prompt_includes_github_connected_status(self) -> None:
        status = GitHubConnectionCheck(
            status=GitHubConnectionStatus.CONNECTED,
            username="octocat",
            repos_available=True,
        )
        prompt = build_intake_system_prompt(github_status=status)
        assert "GITHUB CONNECTION STATUS:" in prompt
        assert "@octocat" in prompt
        assert "suggest repos" in prompt.lower()

    def test_prompt_includes_github_expired_status(self) -> None:
        status = GitHubConnectionCheck(
            status=GitHubConnectionStatus.EXPIRED,
            username="octocat",
            repos_available=False,
        )
        prompt = build_intake_system_prompt(github_status=status)
        assert "GITHUB CONNECTION STATUS:" in prompt
        assert "expired" in prompt.lower()
        assert "github_reauth" in prompt

    def test_prompt_includes_github_not_connected_status(self) -> None:
        status = GitHubConnectionCheck(
            status=GitHubConnectionStatus.NOT_CONNECTED,
            repos_available=False,
        )
        prompt = build_intake_system_prompt(github_status=status)
        assert "GITHUB CONNECTION STATUS:" in prompt
        assert "not connected" in prompt.lower()
        assert "github_connect" in prompt

    def test_prompt_includes_github_rate_limited_status(self) -> None:
        status = GitHubConnectionCheck(
            status=GitHubConnectionStatus.RATE_LIMITED,
            username="octocat",
            repos_available=False,
        )
        prompt = build_intake_system_prompt(github_status=status)
        assert "GITHUB CONNECTION STATUS:" in prompt
        assert "rate-limited" in prompt.lower() or "rate limited" in prompt.lower()

    def test_prompt_excludes_github_section_when_no_status(self) -> None:
        prompt = build_intake_system_prompt(github_status=None)
        assert "GITHUB CONNECTION STATUS:" not in prompt
