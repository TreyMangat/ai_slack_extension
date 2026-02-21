"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-20 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("requester_user_id", sa.String(length=64), nullable=False),
        sa.Column("slack_channel_id", sa.String(length=64), nullable=False),
        sa.Column("slack_thread_ts", sa.String(length=64), nullable=False),
        sa.Column("slack_message_ts", sa.String(length=64), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("github_issue_url", sa.Text(), nullable=False),
        sa.Column("github_pr_url", sa.Text(), nullable=False),
        sa.Column("preview_url", sa.Text(), nullable=False),
        sa.Column("product_approved_by", sa.String(length=128), nullable=False),
        sa.Column("product_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feature_requests_status", "feature_requests", ["status"], unique=False)

    op.create_table(
        "feature_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["feature_id"], ["feature_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feature_events_feature_id", "feature_events", ["feature_id"], unique=False)
    op.create_index("ix_feature_events_feature_created", "feature_events", ["feature_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feature_events_feature_created", table_name="feature_events")
    op.drop_index("ix_feature_events_feature_id", table_name="feature_events")
    op.drop_table("feature_events")
    op.drop_index("ix_feature_requests_status", table_name="feature_requests")
    op.drop_table("feature_requests")
