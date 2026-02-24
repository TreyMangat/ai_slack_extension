from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field
from typing_extensions import Literal


class FeatureSpec(BaseModel):
    title: str = Field(..., max_length=200)
    problem: str = Field(..., description="What problem are we solving?")
    business_justification: str = Field(
        default="",
        description="Why this matters now (impact, urgency, and expected outcome).",
    )
    proposed_solution: str = Field(default="", description="Suggested approach (optional)")
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)

    repo: str = Field(default="", description="repo identifier, e.g. org/repo")
    base_branch: str = Field(default="", description="optional PR base branch override")
    implementation_mode: Literal["new_feature", "reuse_existing"] = "new_feature"
    source_repos: list[str] = Field(
        default_factory=list,
        description="Referenced source repos when implementation_mode=reuse_existing",
    )
    risk_flags: list[str] = Field(default_factory=list, description="e.g. auth, payments, migrations")

    # Optional extras
    links: list[str] = Field(default_factory=list)
    debug_build: bool = False
    ui_feature: bool = False
    ui_keywords: list[str] = Field(default_factory=list)


class FeatureRequestCreate(BaseModel):
    spec: FeatureSpec

    requester_user_id: str = ""

    slack_team_id: str = ""
    slack_channel_id: str = ""
    slack_thread_ts: str = ""
    slack_message_ts: str = ""


class FeatureSpecPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    problem: str | None = None
    business_justification: str | None = None
    proposed_solution: str | None = None
    acceptance_criteria: list[str] | None = None
    non_goals: list[str] | None = None
    repo: str | None = None
    base_branch: str | None = None
    implementation_mode: Literal["new_feature", "reuse_existing"] | None = None
    source_repos: list[str] | None = None
    risk_flags: list[str] | None = None
    links: list[str] | None = None
    debug_build: bool | None = None
    ui_feature: bool | None = None
    ui_keywords: list[str] | None = None


class FeatureSpecUpdateRequest(BaseModel):
    spec: FeatureSpecPatch
    actor_type: str = "user"
    actor_id: str = ""
    message: str = "Spec updated"


class FeatureEventOut(BaseModel):
    id: str
    created_at: datetime

    actor_type: str
    actor_id: str

    event_type: str
    message: str
    data: dict[str, Any]


class FeatureRunOut(BaseModel):
    id: str
    status: str
    runner_type: str
    runner_run_id: str
    actor_id: str
    issue_url: str
    pr_url: str
    preview_url: str
    artifacts: dict[str, Any]
    error_text: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class FeatureRequestOut(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime

    status: str
    title: str

    requester_user_id: str

    slack_team_id: str
    slack_channel_id: str
    slack_thread_ts: str

    spec: dict[str, Any]

    github_issue_url: str
    github_pr_url: str
    preview_url: str
    active_build_job_id: str

    product_approved_by: str
    product_approved_at: Optional[datetime]

    last_error: str

    events: list[FeatureEventOut] = Field(default_factory=list)
    runs: list[FeatureRunOut] = Field(default_factory=list)


class FeatureRequestListOut(BaseModel):
    items: list[FeatureRequestOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    has_more: bool


class TransitionRequest(BaseModel):
    action: str
    actor_type: str = "system"  # system|slack|user
    actor_id: str = ""
    message: str = ""


class BuildRequest(BaseModel):
    """Manual trigger to start a build (useful for local UI)."""

    actor_type: str = "user"
    actor_id: str = ""
    message: str = "Manual build trigger"


class ExecutionCallbackIn(BaseModel):
    """Signed callback payload from external execution systems."""

    feature_id: str = ""
    event: Literal["pr_opened", "preview_ready", "build_failed", "preview_failed"]
    github_pr_url: str = ""
    preview_url: str = ""
    message: str = ""
    actor_id: str = "integration"
    event_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
