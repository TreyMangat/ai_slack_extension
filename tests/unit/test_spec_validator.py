"""Tests for spec_validator.py."""
from __future__ import annotations

from app.services.spec_validator import validate_spec


def test_complete_spec_is_valid():
    spec = {
        "title": "Add export button",
        "problem": "Users need to export data",
        "repo": "org/app",
        "business_justification": "Support teams need offline reporting",
        "acceptance_criteria": ["Button exists", "CSV downloads"],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is True
    assert missing == []
    assert warnings == []


def test_missing_title_is_invalid():
    spec = {
        "problem": "Something",
        "repo": "org/app",
        "business_justification": "Still matters",
        "acceptance_criteria": ["Test"],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is False
    assert "title" in missing


def test_missing_problem_is_invalid():
    spec = {
        "title": "Something",
        "repo": "org/app",
        "business_justification": "Still matters",
        "acceptance_criteria": ["Test"],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is False
    assert "problem" in missing


def test_missing_repo_is_invalid():
    spec = {
        "title": "Something",
        "problem": "Need a thing",
        "acceptance_criteria": ["Test"],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is False
    assert "repo" in missing


def test_missing_business_justification_is_allowed():
    spec = {
        "title": "Something",
        "problem": "Need a thing",
        "repo": "org/app",
        "acceptance_criteria": ["Test"],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is True
    assert missing == []
    assert warnings == []


def test_empty_spec_is_invalid():
    is_valid, missing, _ = validate_spec({})
    assert is_valid is False
    assert len(missing) > 0


def test_whitespace_only_title_is_invalid():
    spec = {
        "title": "   ",
        "problem": "Real problem",
        "repo": "org/app",
        "business_justification": "Important now",
        "acceptance_criteria": ["AC"],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is False
    assert "title" in missing


def test_missing_acceptance_criteria_is_allowed():
    spec = {
        "title": "Add export button",
        "problem": "Users need exports",
        "repo": "org/app",
        "business_justification": "It saves time",
        "acceptance_criteria": [],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is True
    assert "acceptance_criteria" not in missing


def test_invalid_implementation_mode_is_invalid():
    spec = {
        "title": "Add export button",
        "problem": "Users need exports",
        "repo": "org/app",
        "business_justification": "It saves time",
        "acceptance_criteria": ["CSV downloads"],
        "implementation_mode": "mystery_mode",
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is False
    assert "implementation_mode" in missing


def test_reuse_existing_requires_source_repos():
    spec = {
        "title": "Add export button",
        "problem": "Users need exports",
        "repo": "org/app",
        "business_justification": "It saves time",
        "acceptance_criteria": ["CSV downloads"],
        "implementation_mode": "reuse_existing",
        "source_repos": [],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is False
    assert "source_repos" in missing
    assert warnings == []


def test_reuse_existing_without_repo_is_invalid():
    spec = {
        "title": "Add export button",
        "problem": "Users need exports",
        "business_justification": "It saves time",
        "acceptance_criteria": ["CSV downloads"],
        "implementation_mode": "reuse_existing",
        "source_repos": ["org/reference"],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is False
    assert "repo" in missing
    assert warnings == []


def test_new_feature_with_source_repos_warns_only():
    spec = {
        "title": "Add export button",
        "problem": "Users need exports",
        "repo": "org/app",
        "business_justification": "It saves time",
        "acceptance_criteria": ["CSV downloads"],
        "implementation_mode": "new_feature",
        "source_repos": ["org/reference"],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is True
    assert missing == []
    assert "source_repos provided for new_feature mode; they will be treated as references only" in warnings


def test_high_risk_flags_add_warning():
    spec = {
        "title": "Add billing export",
        "problem": "Finance needs exports",
        "repo": "org/app",
        "business_justification": "Monthly close depends on it",
        "acceptance_criteria": ["CSV downloads"],
        "risk_flags": ["payments"],
    }
    is_valid, missing, warnings = validate_spec(spec)
    assert is_valid is True
    assert missing == []
    assert "High-risk flag detected: consider requiring human review" in warnings
