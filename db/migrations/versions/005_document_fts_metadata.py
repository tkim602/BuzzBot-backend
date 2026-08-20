"""Align document FTS index with title, headings, and chunk text queries.

Revision ID: 005
Revises: 004
Create Date: 2026-08-21 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FTS_DOCUMENT_EXPRESSION = (
    "coalesce(title, '') || ' ' || coalesce(headings, '') || ' ' || chunk_text"
)


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_chunk_text_tsv_simple")
    op.execute(
        f"""
        CREATE INDEX ix_chunks_document_tsv_simple
        ON chunks
        USING GIN (to_tsvector('simple', {FTS_DOCUMENT_EXPRESSION}))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_document_tsv_simple")
    op.execute(
        """
        CREATE INDEX ix_chunks_chunk_text_tsv_simple
        ON chunks
        USING GIN (to_tsvector('simple', chunk_text))
        """
    )
