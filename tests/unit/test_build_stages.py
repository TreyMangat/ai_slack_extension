"""Tests for build progress stages and BuildProgress thread safety."""

from __future__ import annotations

from app.tasks.jobs import BUILD_STAGES, BuildProgress, _stage_progress_text


def test_stage_progress_shows_current():
    text = _stage_progress_text("codegen", 45.0)
    assert "Generating code" in text
    assert "hourglass" in text


def test_stage_progress_shows_completed():
    text = _stage_progress_text("codegen", 45.0)
    assert "white_check_mark" in text
    assert "Preparing workspace" in text


def test_stage_progress_shows_pending():
    text = _stage_progress_text("codegen", 45.0)
    assert "white_circle" in text
    assert "Opening pull request" in text


def test_stage_progress_done():
    text = _stage_progress_text("done", 120.0)
    # "done" is the last stage — all prior stages should be checked off
    assert "white_check_mark" in text
    assert "Build complete" in text


def test_build_progress_thread_safety():
    p = BuildProgress()
    p.set_stage("codegen")
    assert p.get_stage() == "codegen"
    p.set_stage("tests")
    assert p.get_stage() == "tests"


def test_stage_progress_includes_elapsed():
    text = _stage_progress_text("repo", 65.0)
    assert "1m" in text or "65" in text


def test_build_stages_list_has_expected_entries():
    keys = [k for k, _ in BUILD_STAGES]
    assert "workspace" in keys
    assert "codegen" in keys
    assert "pr" in keys
    assert "done" in keys
