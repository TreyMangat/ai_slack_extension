from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from app.config import get_settings
from app.services.github_auth import get_github_token_provider

logger = logging.getLogger(__name__)


@dataclass
class PreparedReference:
    source: str
    destination: str
    method: str
    status: str
    detail: str = ""


@dataclass
class WorkspacePreparationResult:
    workspace_id: str
    workspace_root: str
    workspace_path: str
    target_path: str
    references_path: str
    manifest_path: str
    implementation_mode: str
    target_repo: str
    source_repos: list[str]
    prepared_references: list[PreparedReference]
    errors: list[str]

    def to_event_data(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "workspace_root": self.workspace_root,
            "workspace_path": self.workspace_path,
            "target_path": self.target_path,
            "references_path": self.references_path,
            "manifest_path": self.manifest_path,
            "implementation_mode": self.implementation_mode,
            "target_repo": self.target_repo,
            "source_repos": self.source_repos,
            "prepared_references": [r.__dict__ for r in self.prepared_references],
            "errors": self.errors,
        }


@dataclass
class WorkspaceCleanupResult:
    workspace_root: str
    retention_hours: int
    removed_paths: list[str]
    errors: list[str]

    def to_event_data(self) -> dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "retention_hours": self.retention_hours,
            "removed_paths": self.removed_paths,
            "errors": self.errors,
        }


def _safe_slug(value: str, index: int) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip("/").strip())
    cleaned = cleaned.strip("-._")
    if not cleaned:
        cleaned = f"repo-{index}"
    return cleaned[:64]


def _normalize_repo_ref(repo_ref: str) -> str:
    ref = repo_ref.strip()
    if ref.startswith(("http://", "https://", "git@")):
        return ref
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", ref):
        return f"https://github.com/{ref}.git"
    return ""


def _strip_git_metadata(path: Path) -> None:
    for dot_git in path.rglob(".git"):
        if dot_git.is_dir():
            shutil.rmtree(dot_git, ignore_errors=True)


def _is_relative_to(path: Path, maybe_parent: Path) -> bool:
    try:
        path.relative_to(maybe_parent)
        return True
    except ValueError:
        return False


def _resolve_local_repo_path(repo_ref: str, allowed_roots: list[Path]) -> Path | None:
    raw = repo_ref.strip()
    if not raw:
        return None

    roots = [root.resolve() for root in allowed_roots]
    if not roots:
        return None

    direct = Path(raw).expanduser()
    if direct.is_absolute():
        candidate = direct.resolve()
        if not candidate.exists():
            return None
        if any(_is_relative_to(candidate, root) for root in roots):
            return candidate
        return None

    for root in roots:
        candidate = (root / direct).resolve()
        if not candidate.exists():
            continue
        if _is_relative_to(candidate, root):
            return candidate

    return None


def _copy_snapshot(source: Path, destination: Path, ignore_patterns: list[str]) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*ignore_patterns))
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _clone_snapshot(repo_ref: str, destination: Path, *, token: str, timeout_seconds: int) -> str:
    clone_url = _normalize_repo_ref(repo_ref)
    if not clone_url:
        raise ValueError("Unsupported repository reference format for clone")

    safe_clone_url = redact_clone_url_for_logging(clone_url)
    if token and clone_url.startswith("https://github.com/"):
        clone_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        clone_url,
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git clone error"
        stderr = stderr.replace(clone_url, safe_clone_url)
        raise RuntimeError(stderr)

    _strip_git_metadata(destination)
    return safe_clone_url


def redact_clone_url_for_logging(clone_url: str) -> str:
    """Remove credentials from clone URLs before persisting/logging."""

    text = (clone_url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except Exception:
        return text
    if parsed.scheme and parsed.netloc and "@" in parsed.netloc:
        host = parsed.netloc.split("@", 1)[1]
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return text


def _extract_feature_id_from_workspace_dir(path: Path) -> str:
    # Pattern: ff-<feature-uuid>-<unix-ts>
    match = re.match(r"^ff-([0-9a-fA-F-]{36})-\d+$", path.name)
    if not match:
        return ""
    return match.group(1)


def cleanup_old_workspaces(
    *,
    retention_resolver: Callable[[str], int] | None = None,
) -> WorkspaceCleanupResult:
    settings = get_settings()
    workspace_root = Path(settings.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    default_retention_seconds = max(settings.workspace_retention_hours, 1) * 3600
    now = int(time.time())
    removed_paths: list[str] = []
    errors: list[str] = []

    candidates = sorted([p for p in workspace_root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime)
    for path in candidates[: settings.workspace_cleanup_max_per_run]:
        try:
            retention_seconds = default_retention_seconds
            if retention_resolver is not None:
                feature_id = _extract_feature_id_from_workspace_dir(path)
                if feature_id:
                    resolved_hours = retention_resolver(feature_id)
                    if isinstance(resolved_hours, int) and resolved_hours > 0:
                        retention_seconds = resolved_hours * 3600

            age_seconds = now - int(path.stat().st_mtime)
            if age_seconds < retention_seconds:
                continue
            shutil.rmtree(path, ignore_errors=False)
            removed_paths.append(str(path))
        except Exception as e:
            errors.append(f"{path}: {e}")

    if removed_paths:
        logger.info("workspace_cleanup_removed count=%s", len(removed_paths))
    if errors:
        logger.warning("workspace_cleanup_errors count=%s", len(errors))

    return WorkspaceCleanupResult(
        workspace_root=str(workspace_root),
        retention_hours=max(settings.workspace_retention_hours, 1),
        removed_paths=removed_paths,
        errors=errors,
    )


def prepare_workspace(feature_id: str, spec: dict[str, Any]) -> WorkspacePreparationResult:
    settings = get_settings()

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    source_repos = [str(x).strip() for x in (spec.get("source_repos") or []) if str(x).strip()]
    target_repo = str(spec.get("repo", "")).strip()

    workspace_root = Path(settings.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    workspace_id = f"ff-{feature_id}-{int(time.time())}"
    workspace_path = workspace_root / workspace_id
    target_path = workspace_path / "target"
    references_path = workspace_path / "references"
    manifest_path = workspace_path / "workspace_manifest.json"

    target_path.mkdir(parents=True, exist_ok=True)
    references_path.mkdir(parents=True, exist_ok=True)

    prepared_references: list[PreparedReference] = []
    errors: list[str] = []

    if len(source_repos) > settings.workspace_max_source_repos:
        errors.append(
            f"source_repos truncated to {settings.workspace_max_source_repos} entries "
            f"(received {len(source_repos)})"
        )
    repos_to_process = source_repos[: settings.workspace_max_source_repos]

    allowed_copy_root = Path(settings.workspace_local_copy_root).resolve()
    allowed_copy_roots = [allowed_copy_root]
    workspace_root_reference_seed = workspace_root / "references"
    if workspace_root_reference_seed.exists() and workspace_root_reference_seed.is_dir():
        allowed_copy_roots.append(workspace_root_reference_seed)

    ignore_patterns = settings.workspace_copy_ignore_patterns()
    clone_token = ""
    if settings.workspace_enable_git_clone and settings.github_enabled:
        try:
            clone_token = get_github_token_provider().get_token(allow_user_oauth=False)
        except Exception as e:  # noqa: BLE001
            errors.append(f"could not acquire GitHub auth token for clone: {e}")

    for index, repo_ref in enumerate(repos_to_process, start=1):
        slug = _safe_slug(repo_ref, index=index)
        destination = references_path / f"{index:02d}_{slug}"

        local_path = _resolve_local_repo_path(repo_ref, allowed_copy_roots)
        if local_path is not None:
            try:
                _copy_snapshot(local_path, destination, ignore_patterns=ignore_patterns)
                prepared_references.append(
                    PreparedReference(
                        source=repo_ref,
                        destination=str(destination),
                        method="local_copy",
                        status="prepared",
                        detail="snapshot copied from approved local path",
                    )
                )
                continue
            except Exception as e:
                prepared_references.append(
                    PreparedReference(
                        source=repo_ref,
                        destination=str(destination),
                        method="local_copy",
                        status="failed",
                        detail=str(e),
                    )
                )
                errors.append(f"local copy failed for {repo_ref}: {e}")
                continue

        if not settings.workspace_enable_git_clone:
            prepared_references.append(
                PreparedReference(
                    source=repo_ref,
                    destination="",
                    method="git_clone",
                    status="skipped",
                    detail="WORKSPACE_ENABLE_GIT_CLONE=false",
                )
            )
            continue

        if shutil.which("git") is None:
            prepared_references.append(
                PreparedReference(
                    source=repo_ref,
                    destination="",
                    method="git_clone",
                    status="skipped",
                    detail="git binary not available in worker image",
                )
            )
            continue

        try:
            cloned_url = _clone_snapshot(
                repo_ref,
                destination,
                token=clone_token,
                timeout_seconds=settings.workspace_git_clone_timeout_seconds,
            )
            prepared_references.append(
                PreparedReference(
                    source=repo_ref,
                    destination=str(destination),
                    method="git_clone",
                    status="prepared",
                    detail=f"snapshot cloned from {cloned_url}",
                )
            )
        except Exception as e:
            prepared_references.append(
                PreparedReference(
                    source=repo_ref,
                    destination=str(destination),
                    method="git_clone",
                    status="failed",
                    detail=str(e),
                )
            )
            errors.append(f"git clone failed for {repo_ref}: {e}")

    result = WorkspacePreparationResult(
        workspace_id=workspace_id,
        workspace_root=str(workspace_root),
        workspace_path=str(workspace_path),
        target_path=str(target_path),
        references_path=str(references_path),
        manifest_path=str(manifest_path),
        implementation_mode=mode,
        target_repo=target_repo,
        source_repos=source_repos,
        prepared_references=prepared_references,
        errors=errors,
    )

    manifest = {
        "generated_at_unix": int(time.time()),
        "workspace": result.to_event_data(),
        "policy": {
            "isolation_policy": "work in isolated workspace only; source repos are snapshots",
            "direct_push_to_source_repos": "forbidden",
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info(
        "workspace_prepared workspace_id=%s feature_id=%s reference_count=%s",
        result.workspace_id,
        feature_id,
        len(prepared_references),
    )
    return result
