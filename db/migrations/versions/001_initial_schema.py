"""Initial schema — sources, documents, chunks, embeddings, fetch_state.

Revision ID: 001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Sources
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("refresh_policy_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Documents
    op.create_table(
        "documents",
        sa.Column("doc_id", sa.UUID(), primary_key=True),
        sa.Column("source_id", sa.UUID(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("etag", sa.String(256), nullable=True),
        sa.Column("last_modified", sa.String(256), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )

    # Chunks
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.UUID(), primary_key=True),
        sa.Column("doc_id", sa.UUID(), sa.ForeignKey("documents.doc_id"), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("headings", sa.Text(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    op.create_index("ix_chunks_source_id", "chunks", ["source_id"])

    # Embeddings
    op.create_table(
        "embeddings",
        sa.Column(
            "chunk_id",
            sa.UUID(),
            sa.ForeignKey("chunks.chunk_id"),
            primary_key=True,
        ),
        sa.Column("embedding", Vector(1536), nullable=False),
    )

    # Fetch state
    op.create_table(
        "fetch_state",
        sa.Column("url", sa.String(2048), primary_key=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("etag", sa.String(256), nullable=True),
        sa.Column("last_modified", sa.String(256), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("fetch_state")
    op.drop_table("embeddings")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("sources")
    op.execute("DROP EXTENSION IF EXISTS vector")
