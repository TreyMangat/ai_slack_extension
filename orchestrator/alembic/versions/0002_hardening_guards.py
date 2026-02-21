"""hardening guards

Revision ID: 0002_hardening_guards
Revises: 0001_initial_schema
Create Date: 2026-02-20 00:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_hardening_guards"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_requests",
        sa.Column("active_build_job_id", sa.String(length=128), nullable=False, server_default=""),
    )
    op.alter_column("feature_requests", "active_build_job_id", server_default=None)

    op.create_table(
        "integration_callback_receipts",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_integration_callback_receipts_feature_id",
        "integration_callback_receipts",
        ["feature_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_callback_receipts_feature_received",
        "integration_callback_receipts",
        ["feature_id", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_integration_callback_receipts_feature_received", table_name="integration_callback_receipts")
    op.drop_index("ix_integration_callback_receipts_feature_id", table_name="integration_callback_receipts")
    op.drop_table("integration_callback_receipts")
    op.drop_column("feature_requests", "active_build_job_id")

