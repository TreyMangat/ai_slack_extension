from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class FeatureRequest(Base):
    __tablename__ = "feature_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # State machine status
    status: Mapped[str] = mapped_column(String(64), index=True)

    # Minimal metadata
    title: Mapped[str] = mapped_column(String(200))
    requester_user_id: Mapped[str] = mapped_column(String(64), default="")

    # Slack linkage (optional)
    slack_team_id: Mapped[str] = mapped_column(String(64), default="")
    slack_channel_id: Mapped[str] = mapped_column(String(64), default="")
    slack_thread_ts: Mapped[str] = mapped_column(String(64), default="")
    slack_message_ts: Mapped[str] = mapped_column(String(64), default="")

    # The validated spec as JSON
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Execution outputs
    github_issue_url: Mapped[str] = mapped_column(Text, default="")
    github_pr_url: Mapped[str] = mapped_column(Text, default="")
    preview_url: Mapped[str] = mapped_column(Text, default="")
    active_build_job_id: Mapped[str] = mapped_column(String(128), default="")

    # Approvals
    product_approved_by: Mapped[str] = mapped_column(String(128), default="")
    product_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Error handling
    last_error: Mapped[str] = mapped_column(Text, default="")

    events: Mapped[list[FeatureEvent]] = relationship(
        "FeatureEvent", back_populates="feature", cascade="all, delete-orphan"
    )
    runs: Mapped[list[FeatureRun]] = relationship(
        "FeatureRun",
        back_populates="feature",
        cascade="all, delete-orphan",
    )


class FeatureEvent(Base):
    __tablename__ = "feature_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    feature_id: Mapped[str] = mapped_column(String(36), ForeignKey("feature_requests.id"), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    actor_type: Mapped[str] = mapped_column(String(32), default="system")  # system|slack|user
    actor_id: Mapped[str] = mapped_column(String(128), default="")

    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)

    feature: Mapped[FeatureRequest] = relationship("FeatureRequest", back_populates="events")


Index("ix_feature_events_feature_created", FeatureEvent.feature_id, FeatureEvent.created_at)


class FeatureRun(Base):
    __tablename__ = "feature_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    feature_id: Mapped[str] = mapped_column(String(36), ForeignKey("feature_requests.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="QUEUED")
    runner_type: Mapped[str] = mapped_column(String(64), default="")
    runner_run_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    actor_id: Mapped[str] = mapped_column(String(128), default="")
    issue_url: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str] = mapped_column(Text, default="")
    preview_url: Mapped[str] = mapped_column(Text, default="")
    artifacts: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    feature: Mapped[FeatureRequest] = relationship("FeatureRequest", back_populates="runs")


Index("ix_feature_runs_feature_created", FeatureRun.feature_id, FeatureRun.created_at)


class IntegrationCallbackReceipt(Base):
    __tablename__ = "integration_callback_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    feature_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


Index(
    "ix_integration_callback_receipts_feature_received",
    IntegrationCallbackReceipt.feature_id,
    IntegrationCallbackReceipt.received_at,
)


class SlackIntakeSession(Base):
    __tablename__ = "slack_intake_sessions"

    session_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="create")
    feature_id: Mapped[str] = mapped_column(String(36), default="")
    team_id: Mapped[str] = mapped_column(String(64), default="")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    thread_ts: Mapped[str] = mapped_column(String(64), default="")
    message_ts: Mapped[str] = mapped_column(String(64), default="")
    queue: Mapped[list] = mapped_column(JSONB, default=list)
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    asked_fields: Mapped[list] = mapped_column(JSONB, default=list)
    base_spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


Index("ix_slack_intake_sessions_updated_at", SlackIntakeSession.updated_at)


class GitHubUserConnection(Base):
    __tablename__ = "github_user_connections"
    __table_args__ = (
        UniqueConstraint(
            "slack_team_id",
            "slack_user_id",
            name="uq_github_user_connections_team_user",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slack_team_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    slack_user_id: Mapped[str] = mapped_column(String(64), index=True)
    github_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    github_login: Mapped[str] = mapped_column(String(128), default="", index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    token_scope: Mapped[str] = mapped_column(String(512), default="")
    token_type: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
