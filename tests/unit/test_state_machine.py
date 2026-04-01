from __future__ import annotations

import pytest

from app.state_machine import (
    BUILDING,
    FAILED_BUILD,
    FAILED_PREVIEW,
    FAILED_SPEC,
    FAILURE_STATES,
    MERGED,
    NEEDS_HUMAN,
    NEEDS_INFO,
    NEW,
    PR_OPENED,
    PREVIEW_READY,
    PRODUCT_APPROVED,
    READY_FOR_BUILD,
    READY_TO_MERGE,
    TERMINAL_STATES,
    FeatureStatus,
    perform_action,
    validate_transition,
)


# --- Enum tests ---


def test_feature_status_is_str_enum():
    assert isinstance(FeatureStatus.NEW, str)
    assert FeatureStatus.NEW == "NEW"


def test_all_states_in_enum():
    assert len(FeatureStatus) == 13


def test_terminal_states():
    assert TERMINAL_STATES == {MERGED}


def test_failure_states():
    assert FAILURE_STATES == {FAILED_SPEC, FAILED_BUILD, FAILED_PREVIEW, NEEDS_HUMAN}


# --- Happy path transitions ---

HAPPY_PATH = [
    (NEW, NEEDS_INFO),
    (NEW, READY_FOR_BUILD),
    (NEEDS_INFO, READY_FOR_BUILD),
    (READY_FOR_BUILD, BUILDING),
    (BUILDING, PR_OPENED),
    (PR_OPENED, PREVIEW_READY),
    (PREVIEW_READY, PRODUCT_APPROVED),
    (PRODUCT_APPROVED, READY_TO_MERGE),
    (READY_TO_MERGE, MERGED),
]


@pytest.mark.parametrize("current,target", HAPPY_PATH)
def test_valid_happy_path_transitions(current, target):
    validate_transition(current, target)  # should not raise


# --- Failure transitions ---

FAILURE_TRANSITIONS = [
    (NEW, FAILED_SPEC),
    (NEEDS_INFO, FAILED_SPEC),
    (BUILDING, FAILED_BUILD),
    (PR_OPENED, FAILED_PREVIEW),
    (PR_OPENED, FAILED_BUILD),
    (READY_TO_MERGE, NEEDS_HUMAN),
]


@pytest.mark.parametrize("current,target", FAILURE_TRANSITIONS)
def test_valid_failure_transitions(current, target):
    validate_transition(current, target)


# --- Recovery transitions ---

RECOVERY_TRANSITIONS = [
    (FAILED_SPEC, NEEDS_INFO),
    (FAILED_SPEC, READY_FOR_BUILD),
    (FAILED_BUILD, READY_FOR_BUILD),
    (FAILED_BUILD, NEEDS_HUMAN),
    (FAILED_PREVIEW, READY_FOR_BUILD),
    (FAILED_PREVIEW, NEEDS_HUMAN),
    (NEEDS_HUMAN, READY_FOR_BUILD),
    (NEEDS_HUMAN, NEEDS_INFO),
]


@pytest.mark.parametrize("current,target", RECOVERY_TRANSITIONS)
def test_valid_recovery_transitions(current, target):
    validate_transition(current, target)


# --- Invalid transitions ---

INVALID_TRANSITIONS = [
    (MERGED, NEW),
    (MERGED, BUILDING),
    (NEW, BUILDING),
    (NEW, PR_OPENED),
    (BUILDING, MERGED),
    (READY_FOR_BUILD, MERGED),
    (PREVIEW_READY, BUILDING),
    (NEEDS_INFO, BUILDING),
]


@pytest.mark.parametrize("current,target", INVALID_TRANSITIONS)
def test_invalid_transitions_raise(current, target):
    with pytest.raises(ValueError):
        validate_transition(current, target)


# --- Terminal state has no exits ---


def test_merged_has_no_valid_transitions():
    for state in FeatureStatus:
        if state == MERGED:
            continue
        with pytest.raises(ValueError):
            validate_transition(MERGED, state)


# --- perform_action tests ---


def test_validate_spec_true():
    result = perform_action(NEW, "validate_spec", spec_valid=True)
    assert result.new_status == READY_FOR_BUILD


def test_validate_spec_false():
    result = perform_action(NEW, "validate_spec", spec_valid=False)
    assert result.new_status == NEEDS_INFO


def test_validate_spec_requires_spec_valid():
    with pytest.raises(ValueError):
        perform_action(NEW, "validate_spec")


def test_start_build_action():
    result = perform_action(READY_FOR_BUILD, "start_build")
    assert result.new_status == BUILDING


def test_opened_pr_action():
    result = perform_action(BUILDING, "opened_pr")
    assert result.new_status == PR_OPENED


def test_approve_action():
    result = perform_action(PREVIEW_READY, "approve")
    assert result.new_status == PRODUCT_APPROVED


def test_merge_action():
    result = perform_action(READY_TO_MERGE, "merge")
    assert result.new_status == MERGED


def test_unknown_action_raises():
    with pytest.raises(ValueError, match="Unknown action"):
        perform_action(NEW, "nonexistent_action")
