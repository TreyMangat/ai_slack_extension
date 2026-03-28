"""add llm_spec_analysis column to feature_requests

Revision ID: 0007_llm_spec_analysis
Revises: 0006_feature_runs
Create Date: 2026-03-27 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0007_llm_spec_analysis"
down_revision = "0006_feature_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_requests",
        sa.Column("llm_spec_analysis", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("feature_requests", "llm_spec_analysis")
