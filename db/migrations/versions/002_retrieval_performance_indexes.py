"""Add retrieval performance indexes for vector/FTS/metadata filters.

Revision ID: 002
Revises: 001
Create Date: 2026-02-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Vector ANN index for cosine distance search.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_embeddings_embedding_ivfflat_cosine
        ON embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 32)
        """
    )

    # Expression GIN index for lexical retrieval.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_chunk_text_tsv_simple
        ON chunks
        USING GIN (to_tsvector('simple', chunk_text))
        """
    )

    # Metadata lookup acceleration for course schedule filters.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_source_course_term
        ON chunks (
            source_id,
            (upper(metadata_json->>'course_code')),
            (lower(metadata_json->>'term_name'))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_source_course_term")
    op.execute("DROP INDEX IF EXISTS ix_chunks_chunk_text_tsv_simple")
    op.execute("DROP INDEX IF EXISTS ix_embeddings_embedding_ivfflat_cosine")
