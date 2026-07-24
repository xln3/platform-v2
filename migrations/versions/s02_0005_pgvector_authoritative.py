"""Enable authoritative pgvector hybrid retrieval.

Revision ID: s02_0005
Revises: s02_0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0005"
down_revision: str | None = "s02_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS vector;
        ALTER TABLE intelligence.content_version
          DROP COLUMN IF EXISTS embedding_vector;
        ALTER TABLE intelligence.content_version
          ADD COLUMN embedding_vector vector(384);
        CREATE INDEX content_version_embedding_hnsw_idx
          ON intelligence.content_version
          USING hnsw (embedding_vector vector_cosine_ops);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS intelligence.content_version_embedding_hnsw_idx;
        ALTER TABLE intelligence.content_version DROP COLUMN IF EXISTS embedding_vector;
        """
    )
