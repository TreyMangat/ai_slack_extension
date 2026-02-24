"""feature run tracking table

Revision ID: 0006_feature_runs
Revises: 0005_github_user_connections
Create Date: 2026-02-24 00:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0006_feature_runs"
down_revision = "0005_github_user_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "feature_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("runner_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("runner_run_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("actor_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("issue_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("pr_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("preview_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("artifacts", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["feature_id"], ["feature_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("feature_runs", "status", server_default=None)
    op.alter_column("feature_runs", "runner_type", server_default=None)
    op.alter_column("feature_runs", "runner_run_id", server_default=None)
    op.alter_column("feature_runs", "actor_id", server_default=None)
    op.alter_column("feature_runs", "issue_url", server_default=None)
    op.alter_column("feature_runs", "pr_url", server_default=None)
    op.alter_column("feature_runs", "preview_url", server_default=None)
    op.alter_column("feature_runs", "artifacts", server_default=None)
    op.alter_column("feature_runs", "error_text", server_default=None)
    op.create_index("ix_feature_runs_feature_id", "feature_runs", ["feature_id"], unique=False)
    op.create_index("ix_feature_runs_status", "feature_runs", ["status"], unique=False)
    op.create_index("ix_feature_runs_runner_run_id", "feature_runs", ["runner_run_id"], unique=False)
    op.create_index(
        "ix_feature_runs_feature_created",
        "feature_runs",
        ["feature_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feature_runs_feature_created", table_name="feature_runs")
    op.drop_index("ix_feature_runs_runner_run_id", table_name="feature_runs")
    op.drop_index("ix_feature_runs_status", table_name="feature_runs")
    op.drop_index("ix_feature_runs_feature_id", table_name="feature_runs")
    op.drop_table("feature_runs")
