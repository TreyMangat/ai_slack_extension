from __future__ import annotations

import pytest

from app.state_machine import BUILDING, NEW, READY_FOR_BUILD, perform_action, validate_transition


def test_validate_transition_allows_new_to_ready_for_build() -> None:
    validate_transition(NEW, READY_FOR_BUILD)


def test_validate_transition_rejects_invalid_path() -> None:
    with pytest.raises(ValueError):
        validate_transition(BUILDING, READY_FOR_BUILD)


def test_perform_action_validate_spec_true_sets_ready() -> None:
    result = perform_action(NEW, "validate_spec", spec_valid=True)
    assert result.new_status == READY_FOR_BUILD

