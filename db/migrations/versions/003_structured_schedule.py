"""Add versioned academic schedule tables.

Revision ID: 003
Revises: 002
Create Date: 2026-08-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("requested_unit", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("row_counts_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_data_versions_published_lookup",
        "data_versions",
        ["provider", "requested_unit", "status", "published_at"],
    )

    op.create_table(
        "academic_terms",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "data_version_id", "term_code", name="uq_academic_terms_version_code"
        ),
    )

    op.create_table(
        "courses",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(16), nullable=False),
        sa.Column("course_number", sa.String(16), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("credits", sa.Float(), nullable=False),
        sa.Column("prerequisites_json", sa.JSON(), nullable=True),
        # The unique constraint's backing index is the course-key lookup index.
        sa.UniqueConstraint(
            "data_version_id",
            "subject",
            "course_number",
            name="uq_courses_version_subject_number",
        ),
    )

    op.create_table(
        "sections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("academic_term_id", sa.UUID(), sa.ForeignKey("academic_terms.id"), nullable=False),
        sa.Column("course_id", sa.UUID(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("term_code", sa.String(16), nullable=False),
        sa.Column("crn", sa.String(16), nullable=False),
        sa.Column("section_code", sa.String(16), nullable=False),
        sa.Column("campus", sa.String(128), nullable=False),
        sa.Column("schedule_type", sa.String(128), nullable=False),
        sa.Column("instructional_method", sa.String(128), nullable=True),
        sa.Column("instructors_json", postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        # The unique constraint's backing index is the term/CRN lookup index.
        sa.UniqueConstraint(
            "data_version_id", "term_code", "crn", name="uq_sections_version_term_crn"
        ),
    )
    op.create_index(
        "ix_sections_instructors_json",
        "sections",
        ["instructors_json"],
        postgresql_using="gin",
    )

    op.create_table(
        "meetings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_id", sa.UUID(), sa.ForeignKey("sections.id"), nullable=False),
        sa.Column("meeting_type", sa.String(64), nullable=False),
        sa.Column("days", sa.String(16), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("building", sa.String(256), nullable=True),
        sa.Column("room", sa.String(64), nullable=True),
        sa.Column("is_tba", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_meetings_days_times", "meetings", ["days", "start_time", "end_time"]
    )

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(256), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("raw_location", sa.String(2048), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
    )

    op.create_table(
        "ingestion_errors",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "data_version_id",
            sa.UUID(),
            sa.ForeignKey("data_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ingestion_errors")
    op.drop_table("source_snapshots")
    op.drop_index("ix_meetings_days_times", table_name="meetings")
    op.drop_table("meetings")
    op.drop_index("ix_sections_instructors_json", table_name="sections")
    op.drop_table("sections")
    op.drop_table("courses")
    op.drop_table("academic_terms")
    op.drop_index("ix_data_versions_published_lookup", table_name="data_versions")
    op.drop_table("data_versions")
