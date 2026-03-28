from __future__ import annotations

import re

import pytest

from app.tasks.jobs import _extract_pr_number


class TestExtractPrNumber:
    def test_standard_github_url(self) -> None:
        assert _extract_pr_number("https://github.com/org/repo/pull/42") == 42

    def test_no_match(self) -> None:
        assert _extract_pr_number("https://github.com/org/repo") is None

    def test_empty_string(self) -> None:
        assert _extract_pr_number("") is None

    def test_none_value(self) -> None:
        assert _extract_pr_number(None) is None

    def test_trailing_path(self) -> None:
        assert _extract_pr_number("https://github.com/org/repo/pull/99/files") == 99
