"""Own LangGraph checkpoint tables through Alembic instead of API startup.

Revision ID: 006
Revises: 005
Create Date: 2026-08-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint JSONB NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            blob BYTEA NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
        """
    )
    op.execute(
        "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''"
    )
    op.execute("CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)"
    )
    op.execute(
        "INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, 9) ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_index("checkpoint_writes_thread_id_idx", table_name="checkpoint_writes")
    op.drop_index("checkpoint_blobs_thread_id_idx", table_name="checkpoint_blobs")
    op.drop_index("checkpoints_thread_id_idx", table_name="checkpoints")
    op.drop_table("checkpoint_writes")
    op.drop_table("checkpoint_blobs")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_migrations")
