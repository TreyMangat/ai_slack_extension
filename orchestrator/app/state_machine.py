from __future__ import annotations

from dataclasses import dataclass


# States
NEW = "NEW"
NEEDS_INFO = "NEEDS_INFO"
READY_FOR_BUILD = "READY_FOR_BUILD"
BUILDING = "BUILDING"
PR_OPENED = "PR_OPENED"
PREVIEW_READY = "PREVIEW_READY"
PRODUCT_APPROVED = "PRODUCT_APPROVED"
READY_TO_MERGE = "READY_TO_MERGE"
MERGED = "MERGED"

FAILED_SPEC = "FAILED_SPEC"
FAILED_BUILD = "FAILED_BUILD"
FAILED_PREVIEW = "FAILED_PREVIEW"
NEEDS_HUMAN = "NEEDS_HUMAN"


TERMINAL_STATES = {MERGED}
FAILURE_STATES = {FAILED_SPEC, FAILED_BUILD, FAILED_PREVIEW, NEEDS_HUMAN}


@dataclass(frozen=True)
class ActionResult:
    new_status: str
    message: str = ""


def validate_transition(current: str, new: str) -> None:
    allowed = {
        NEW: {NEEDS_INFO, READY_FOR_BUILD, FAILED_SPEC},
        NEEDS_INFO: {READY_FOR_BUILD, FAILED_SPEC},
        READY_FOR_BUILD: {BUILDING, NEEDS_INFO},
        BUILDING: {PR_OPENED, FAILED_BUILD},
        PR_OPENED: {PREVIEW_READY, FAILED_PREVIEW, FAILED_BUILD},
        PREVIEW_READY: {PRODUCT_APPROVED, NEEDS_INFO},
        PRODUCT_APPROVED: {READY_TO_MERGE, NEEDS_INFO},
        READY_TO_MERGE: {MERGED, NEEDS_HUMAN},
        MERGED: set(),
        FAILED_SPEC: {NEEDS_INFO, READY_FOR_BUILD},
        FAILED_BUILD: {READY_FOR_BUILD, NEEDS_HUMAN},
        FAILED_PREVIEW: {READY_FOR_BUILD, NEEDS_HUMAN},
        NEEDS_HUMAN: {READY_FOR_BUILD, NEEDS_INFO},
    }.get(current)

    if allowed is None:
        raise ValueError(f"Unknown current state: {current}")

    if new not in allowed:
        raise ValueError(f"Invalid transition: {current} -> {new}")


def perform_action(current: str, action: str, *, spec_valid: bool | None = None) -> ActionResult:
    """Convert an action (event) into a state transition.

    We keep this simple for the scaffold.
    """

    action = action.lower().strip()

    if action == "validate_spec":
        if spec_valid is None:
            raise ValueError("spec_valid is required for validate_spec")
        return ActionResult(new_status=READY_FOR_BUILD if spec_valid else NEEDS_INFO)

    if action == "start_build":
        return ActionResult(new_status=BUILDING)

    if action == "opened_pr":
        return ActionResult(new_status=PR_OPENED)

    if action == "preview_ready":
        return ActionResult(new_status=PREVIEW_READY)

    if action == "fail_build":
        return ActionResult(new_status=FAILED_BUILD)

    if action == "fail_preview":
        return ActionResult(new_status=FAILED_PREVIEW)

    if action == "approve":
        return ActionResult(new_status=PRODUCT_APPROVED)

    if action == "ready_to_merge":
        return ActionResult(new_status=READY_TO_MERGE)

    if action == "merge":
        return ActionResult(new_status=MERGED)

    if action == "needs_human":
        return ActionResult(new_status=NEEDS_HUMAN)

    raise ValueError(f"Unknown action: {action}")
