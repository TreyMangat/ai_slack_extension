"""github user oauth connection table

Revision ID: 0005_github_user_connections
Revises: 0004_slack_oauth_multi_workspace
Create Date: 2026-02-24 00:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_github_user_connections"
down_revision = "0004_slack_oauth_multi_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_user_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slack_team_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("github_user_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("github_login", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("token_scope", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("token_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slack_team_id", "slack_user_id", name="uq_github_user_connections_team_user"),
    )
    op.alter_column("github_user_connections", "slack_team_id", server_default=None)
    op.alter_column("github_user_connections", "github_user_id", server_default=None)
    op.alter_column("github_user_connections", "github_login", server_default=None)
    op.alter_column("github_user_connections", "access_token_encrypted", server_default=None)
    op.alter_column("github_user_connections", "token_scope", server_default=None)
    op.alter_column("github_user_connections", "token_type", server_default=None)
    op.create_index(
        "ix_github_user_connections_slack_team_id",
        "github_user_connections",
        ["slack_team_id"],
        unique=False,
    )
    op.create_index(
        "ix_github_user_connections_slack_user_id",
        "github_user_connections",
        ["slack_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_github_user_connections_github_user_id",
        "github_user_connections",
        ["github_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_github_user_connections_github_login",
        "github_user_connections",
        ["github_login"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_github_user_connections_github_login", table_name="github_user_connections")
    op.drop_index("ix_github_user_connections_github_user_id", table_name="github_user_connections")
    op.drop_index("ix_github_user_connections_slack_user_id", table_name="github_user_connections")
    op.drop_index("ix_github_user_connections_slack_team_id", table_name="github_user_connections")
    op.drop_table("github_user_connections")
