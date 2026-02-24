"""slack oauth multi-workspace fields

Revision ID: 0004_slack_oauth_multi_workspace
Revises: 0003_slack_intake_sessions
Create Date: 2026-02-24 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_slack_oauth_multi_workspace"
down_revision = "0003_slack_intake_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_requests",
        sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("feature_requests", "slack_team_id", server_default=None)
    op.create_index("ix_feature_requests_slack_team_id", "feature_requests", ["slack_team_id"], unique=False)

    op.add_column(
        "slack_intake_sessions",
        sa.Column("team_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("slack_intake_sessions", "team_id", server_default=None)
    op.create_index("ix_slack_intake_sessions_team_id", "slack_intake_sessions", ["team_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_slack_intake_sessions_team_id", table_name="slack_intake_sessions")
    op.drop_column("slack_intake_sessions", "team_id")

    op.drop_index("ix_feature_requests_slack_team_id", table_name="feature_requests")
    op.drop_column("feature_requests", "slack_team_id")
