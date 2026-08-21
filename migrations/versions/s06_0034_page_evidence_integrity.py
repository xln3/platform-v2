"""Harden exact page evidence spans and project-wide repost lookup.

Revision ID: s06_0034
Revises: s06_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s06_0034"
down_revision: str | Sequence[str] | None = "s06_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "quote_interval_length",
        "page_evidence_span",
        "char_length(quote) = text_end - text_start",
        schema="platform",
    )
    op.create_index(
        "ix_source_document_project_text_sha256",
        "source_document",
        ["project_id", "text_sha256"],
        schema="platform",
        postgresql_where=sa.text("text_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_document_project_text_sha256",
        table_name="source_document",
        schema="platform",
    )
    op.drop_constraint(
        "ck_page_evidence_span_quote_interval_length",
        "page_evidence_span",
        schema="platform",
        type_="check",
    )


__all__ = ["downgrade", "upgrade"]
