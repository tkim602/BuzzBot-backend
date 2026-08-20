"""Add resumable ingestion run manifests.

Revision ID: 004
Revises: 003
Create Date: 2026-08-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("retry_limit", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'PAUSED', 'COMPLETED', 'PARTIAL', 'FAILED')",
            name="ck_ingestion_runs_status",
        ),
        sa.CheckConstraint("concurrency > 0", name="ck_ingestion_runs_concurrency"),
        sa.CheckConstraint("retry_limit >= 0", name="ck_ingestion_runs_retry_limit"),
    )
    op.create_table(
        "ingestion_run_units",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "run_id",
            sa.UUID(),
            sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unit_key", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("published_version_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_ingestion_run_units_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ingestion_run_units_attempts"),
        sa.CheckConstraint("position >= 0", name="ck_ingestion_run_units_position"),
        sa.UniqueConstraint("run_id", "unit_key", name="uq_ingestion_run_units_key"),
        sa.UniqueConstraint("run_id", "position", name="uq_ingestion_run_units_position"),
    )
    op.create_index(
        "ix_ingestion_run_units_pending",
        "ingestion_run_units",
        ["run_id", "status", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_run_units_pending", table_name="ingestion_run_units")
    op.drop_table("ingestion_run_units")
    op.drop_table("ingestion_runs")
