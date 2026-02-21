"""slack intake sessions

Revision ID: 0003_slack_intake_sessions
Revises: 0002_hardening_guards
Create Date: 2026-02-21 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0003_slack_intake_sessions"
down_revision = "0002_hardening_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slack_intake_sessions",
        sa.Column("session_key", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("thread_ts", sa.String(length=64), nullable=False),
        sa.Column("message_ts", sa.String(length=64), nullable=False),
        sa.Column("queue", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("asked_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_key"),
    )
    op.create_index("ix_slack_intake_sessions_expires_at", "slack_intake_sessions", ["expires_at"], unique=False)
    op.create_index("ix_slack_intake_sessions_updated_at", "slack_intake_sessions", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_slack_intake_sessions_updated_at", table_name="slack_intake_sessions")
    op.drop_index("ix_slack_intake_sessions_expires_at", table_name="slack_intake_sessions")
    op.drop_table("slack_intake_sessions")
