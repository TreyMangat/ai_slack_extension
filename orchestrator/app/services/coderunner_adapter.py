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

from app.config import get_settings
from app.services.github_auth import get_github_token_provider
from app.services.github_adapter import GitHubAdapter
from app.services.llm_provider import LLMProvider, LLMProviderError
from app.services.pr_description import build_standard_pr_body
from app.services.prompt_optimizer import build_optimized_prompt, detect_ui_feature


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
        fake_pr = f"https://example.local/github/pull/{issue_number}"
        fake_preview = f"http://localhost:8000/preview/{issue_number}"
        mode = (build_context or {}).get("implementation_mode", "new_feature")
        console.print(
            f"[bold cyan][MOCK CodeRunner][/bold cyan] kickoff issue #{issue_number} mode={mode} -> PR {fake_pr}"
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
) -> None:
    clone_url = f"https://github.com/{owner}/{repo}.git"
    auth_header = _github_basic_auth_header(token)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader={auth_header}",
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
    issue_number: int,
    spec: dict[str, Any],
) -> str:
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    title = str(spec.get("title") or "").strip() or "(untitled feature)"
    lines = [
        "# DEBUG_CODEGEN",
        "",
        "Generated by Feature Factory debug build mode.",
        "",
        f"- generated_at_utc: {timestamp}",
        f"- feature_id: {feature_id or '(unknown)'}",
        f"- issue_number: {issue_number}",
        f"- title: {title}",
    ]
    target = repo_path / "DEBUG_CODEGEN.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return timestamp


def _find_frontend_root(repo_path: Path) -> Path | None:
    for candidate in ["web", "ui"]:
        pkg = repo_path / candidate / "package.json"
        if pkg.exists():
            return pkg.parent
    root_pkg = repo_path / "package.json"
    if root_pkg.exists():
        return repo_path
    return None


def _write_minimal_vite_react_app(*, repo_path: Path, spec: dict[str, Any]) -> Path:
    web_root = repo_path / "web"
    src_root = web_root / "src"
    src_root.mkdir(parents=True, exist_ok=True)

    title = str(spec.get("title") or "Feature Demo").strip() or "Feature Demo"
    problem = str(spec.get("problem") or "").strip() or "No detailed problem statement provided."
    criteria = [str(x).strip() for x in (spec.get("acceptance_criteria") or []) if str(x).strip()]
    criteria_js = ",\n  ".join([json.dumps(item) for item in criteria]) if criteria else json.dumps("Review requested behavior")

    package_json = {
        "name": "feature-factory-web",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview --host 0.0.0.0 --port 4173",
        },
        "dependencies": {
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        },
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.4",
            "vite": "^5.4.10",
        },
    }

    (web_root / "package.json").write_text(json.dumps(package_json, indent=2) + "\n", encoding="utf-8")
    (web_root / "index.html").write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "  <head>",
                '    <meta charset="UTF-8" />',
                '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
                f"    <title>{title}</title>",
                "  </head>",
                "  <body>",
                '    <div id="root"></div>',
                '    <script type="module" src="/src/main.jsx"></script>',
                "  </body>",
                "</html>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (web_root / "vite.config.js").write_text(
        "\n".join(
            [
                'import { defineConfig } from "vite";',
                'import react from "@vitejs/plugin-react";',
                "",
                "export default defineConfig({",
                "  plugins: [react()],",
                "});",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (web_root / ".gitignore").write_text(
        "\n".join(
            [
                "node_modules/",
                "dist/",
                ".DS_Store",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (src_root / "main.jsx").write_text(
        "\n".join(
            [
                'import React from "react";',
                'import ReactDOM from "react-dom/client";',
                'import App from "./App";',
                'import "./styles.css";',
                "",
                'ReactDOM.createRoot(document.getElementById("root")).render(',
                "  <React.StrictMode>",
                "    <App />",
                "  </React.StrictMode>,",
                ");",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (src_root / "App.jsx").write_text(
        "\n".join(
            [
                "const acceptanceCriteria = [",
                f"  {criteria_js}",
                "];",
                "",
                "export default function App() {",
                "  return (",
                '    <main className="page">',
                f"      <h1>{title}</h1>",
                f"      <p className=\"problem\">{problem}</p>",
                "      <section>",
                "        <h2>Acceptance Criteria</h2>",
                "        <ul>",
                "          {acceptanceCriteria.map((item) => (",
                "            <li key={item}>{item}</li>",
                "          ))}",
                "        </ul>",
                "      </section>",
                "    </main>",
                "  );",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (src_root / "styles.css").write_text(
        "\n".join(
            [
                ":root {",
                "  color: #101828;",
                "  background: linear-gradient(160deg, #f5f7fa 0%, #e8eef9 100%);",
                "  font-family: \"Segoe UI\", \"Helvetica Neue\", sans-serif;",
                "}",
                "",
                "body {",
                "  margin: 0;",
                "  min-height: 100vh;",
                "}",
                "",
                ".page {",
                "  max-width: 820px;",
                "  margin: 48px auto;",
                "  padding: 28px;",
                "  border-radius: 16px;",
                "  background: #ffffffcc;",
                "  box-shadow: 0 20px 48px rgba(16, 24, 40, 0.14);",
                "}",
                "",
                "h1 {",
                "  margin-top: 0;",
                "  margin-bottom: 10px;",
                "}",
                "",
                ".problem {",
                "  margin-top: 0;",
                "  margin-bottom: 20px;",
                "}",
                "",
                "li {",
                "  margin-bottom: 6px;",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return web_root


def _ensure_ui_scaffold(repo_path: Path, spec: dict[str, Any]) -> tuple[Path | None, bool]:
    ui_feature, _keywords = detect_ui_feature(spec)
    if not ui_feature and not bool(spec.get("ui_feature")):
        return None, False

    existing = _find_frontend_root(repo_path)
    if existing is not None:
        return existing, False

    created = _write_minimal_vite_react_app(repo_path=repo_path, spec=spec)
    return created, True


def _npm_build_command(frontend_root: Path, repo_path: Path) -> str:
    relative = frontend_root.relative_to(repo_path)
    prefix = "." if str(relative) == "." else str(relative).replace("\\", "/")
    has_lockfile = (frontend_root / "package-lock.json").exists() or (frontend_root / "npm-shrinkwrap.json").exists()
    install_cmd = "npm ci" if has_lockfile else "npm install"
    if prefix == ".":
        return f"{install_cmd} && npm run build"
    return f"cd {prefix} && {install_cmd} && npm run build"


def _build_local_openclaw_prompt(
    *,
    issue_number: int,
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
    ui_feature, ui_keywords = detect_ui_feature(spec)
    ui_block = ""
    if ui_feature or bool(spec.get("ui_feature")):
        keywords_text = ", ".join(ui_keywords) if ui_keywords else "(none)"
        ui_block = (
            "\nUI requirements:\n"
            f"- Request classified as UI-focused (keywords: {keywords_text}).\n"
            "- If no web frontend exists, create a minimal Vite + React app under `web/` with dev/build/preview scripts.\n"
            "- Ensure reviewers can use Cloudflare Pages PR preview checks to click and view the UI.\n"
            "- Include a clear PR body section describing where to find preview deployment links.\n"
        )

    return (
        "You are running as an autonomous coding agent inside the target repository workspace.\n"
        "Make direct file edits and execute commands to implement the request safely.\n\n"
        f"Feature request id: {feature_id or '(unknown)'}\n"
        f"Issue number: #{issue_number}\n\n"
        "Structured request:\n"
        f"{optimized_prompt}\n\n"
        "Reference snapshots (read-only context):\n"
        f"{refs_block}\n\n"
        "Execution requirements:\n"
        "- Keep changes scoped to the request and acceptance criteria.\n"
        "- Add or update tests for behavior changes.\n"
        "- Run relevant lint/test commands before finishing.\n"
        "- Do not push to remote and do not open PR yourself.\n"
        f"- Verification command expected by orchestrator: {test_line}\n\n"
        f"{ui_block}\n"
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
        if mode in {"", "github_issue_comment", "issue_comment", "delegated"}:
            return await self._kickoff_github_issue_comment(
                github=github,
                issue_number=issue_number,
                trigger_comment=trigger_comment,
                build_context=build_context,
                spec=spec or {},
            )
        if mode == "local_openclaw":
            return await self._kickoff_local_openclaw(
                github=github,
                issue_number=issue_number,
                build_context=build_context,
                spec=spec,
                feature_id=feature_id,
            )
        raise RuntimeError(f"Unsupported OPENCODE_EXECUTION_MODE: {self.settings.opencode_execution_mode}")

    async def _kickoff_github_issue_comment(
        self,
        *,
        github: GitHubAdapter,
        issue_number: int,
        trigger_comment: str,
        build_context: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
    ) -> CodeRunResult:
        final_spec = spec or {}
        mode = (build_context or {}).get("implementation_mode", "new_feature")
        repos = (build_context or {}).get("source_repos") or []
        ui_feature, ui_keywords = detect_ui_feature(final_spec)
        if bool(final_spec.get("ui_feature")):
            ui_feature = True
        ui_keywords_text = ", ".join(ui_keywords) if ui_keywords else "(none)"
        cloudflare_project = (self.settings.cloudflare_pages_project_name or "").strip() or "(configure CLOUDFLARE_PAGES_PROJECT_NAME)"
        cloudflare_prod_branch = (self.settings.cloudflare_pages_production_branch or "").strip() or "main"
        context_lines = [
            "",
            "Build context:",
            f"- implementation_mode: {mode}",
            f"- ui_feature: {str(ui_feature).lower()}",
            f"- ui_keywords: {ui_keywords_text}",
            "- isolation_policy: work in isolated clone/copy only",
            f"- preview_provider: {self.settings.preview_provider_normalized() or 'cloudflare_pages'}",
            f"- cloudflare_pages_project: {cloudflare_project}",
            f"- cloudflare_pages_production_branch: {cloudflare_prod_branch}",
            "- PR body must include: What changed, Why, Acceptance criteria checklist, How to test locally, and Preview instructions.",
        ]
        if repos:
            context_lines.append("- source_repos:")
            context_lines.extend([f"  - {repo}" for repo in repos])

        await github.comment_issue(issue_number=issue_number, body=trigger_comment + "\n" + "\n".join(context_lines))
        console.print(f"[green]Triggered OpenCode via GitHub issue comment on #{issue_number}[/green]")
        return CodeRunResult(
            github_pr_url="",
            preview_url="",
            runner_metadata={
                "runner": "opencode",
                "execution_mode": "github_issue_comment",
                "issue_number": issue_number,
            },
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

        ui_feature, _ui_keywords = detect_ui_feature(final_spec)
        if bool(final_spec.get("ui_feature")):
            ui_feature = True
        final_spec["ui_feature"] = bool(ui_feature)
        frontend_root, scaffold_created = _ensure_ui_scaffold(target_path, final_spec)

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
            issue_number=issue_number,
            feature_id=feature_id,
            spec=final_spec,
            build_context=build_context,
            test_command=test_command,
        )

        agent_suffix = feature_id[:8] if feature_id else str(issue_number)
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
                issue_number=issue_number,
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

        ui_build_output = ""
        if bool(final_spec.get("ui_feature")):
            if frontend_root is None:
                frontend_root = _write_minimal_vite_react_app(repo_path=target_path, spec=final_spec)
                scaffold_created = True
            ui_build_command = _npm_build_command(frontend_root, target_path)
            try:
                ui_build_output = _run_shell(ui_build_command, cwd=target_path, timeout_seconds=1800)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "UI build verification failed. "
                    f"Command `{ui_build_command}` returned error: {e}"
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

        pr_title = str(final_spec.get("title") or "").strip() or f"Feature request #{issue_number}"
        summary_lines: list[str] = []
        if openclaw_summary:
            summary_lines.append(openclaw_summary)
        if scaffold_created:
            summary_lines.append("Created/updated a minimal frontend scaffold for UI preview readiness.")
        summary_text = "\n".join(summary_lines).strip() or "Automated implementation completed."

        verification_text_parts = [openclaw_tests, verification_output, ui_build_output]
        verification_text = "\n".join([x for x in verification_text_parts if str(x or "").strip()])
        pr_body = build_standard_pr_body(
            spec=final_spec,
            feature_id=feature_id,
            issue_number=issue_number,
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
            base=(settings.github_default_branch or "main"),
        )

        await github.comment_issue(
            issue_number=issue_number,
            body=(
                "OpenClaw local runner completed.\n"
                f"- Branch: `{branch_name}`\n"
                f"- PR: {pr_url}\n"
                f"- Model: `{settings.opencode_model}`"
            ),
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
                "issue_number": issue_number,
                "verification_command": test_command,
                "verification_output": _truncate(verification_output, max_chars=2000),
                "ui_build_output": _truncate(ui_build_output, max_chars=2000),
                "ui_feature": bool(final_spec.get("ui_feature")),
                "frontend_root": str(frontend_root) if frontend_root else "",
                "frontend_scaffold_created": bool(scaffold_created),
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

        ui_feature, _ui_keywords = detect_ui_feature(final_spec)
        if bool(final_spec.get("ui_feature")):
            ui_feature = True
        final_spec["ui_feature"] = bool(ui_feature)
        frontend_root, scaffold_created = _ensure_ui_scaffold(target_path, final_spec)

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

        ui_build_output = ""
        if bool(final_spec.get("ui_feature")):
            if frontend_root is None:
                frontend_root = _write_minimal_vite_react_app(repo_path=target_path, spec=final_spec)
                scaffold_created = True
            ui_build_command = _npm_build_command(frontend_root, target_path)
            try:
                ui_build_output = _run_shell(ui_build_command, cwd=target_path, timeout_seconds=1800)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "UI build verification failed. "
                    f"Command `{ui_build_command}` returned error: {e}"
                ) from e

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

        pr_title = str(final_spec.get("title") or "").strip() or f"Feature request #{issue_number}"
        summary_lines: list[str] = []
        if patch_result and patch_result.rationale:
            summary_lines.append(str(patch_result.rationale))
        if scaffold_created:
            summary_lines.append("Created/updated a minimal frontend scaffold for UI preview readiness.")
        summary_text = "\n".join([x for x in summary_lines if str(x or "").strip()]).strip()
        if not summary_text:
            summary_text = "Automated implementation generated by native LLM runner."
        verification_text = "\n".join([x for x in [verification_output, ui_build_output] if str(x or "").strip()])
        pr_body = build_standard_pr_body(
            spec=final_spec,
            feature_id=feature_id,
            issue_number=issue_number,
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
            base=(settings.github_default_branch or "main"),
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
                "ui_feature": bool(final_spec.get("ui_feature")),
                "frontend_root": str(frontend_root) if frontend_root else "",
                "frontend_scaffold_created": bool(scaffold_created),
                "ui_build_output": _truncate(ui_build_output, max_chars=2000),
            },
        )


def get_coderunner_adapter() -> CodeRunnerAdapter:
    settings = get_settings()
    if settings.mock_mode:
        return MockCodeRunnerAdapter()
    if settings.coderunner_mode_normalized() == "native_llm":
        return NativeLLMCodeRunnerAdapter()
    return RealOpenCodeRunnerAdapter()
