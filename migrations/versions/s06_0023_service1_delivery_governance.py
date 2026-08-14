"""Governed release states and Service-1 delivery sidecars.

Revision ID: s06_0023
Revises: s06_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0023"
down_revision: str | Sequence[str] | None = "s06_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE reporting.formal_report_production
          ADD COLUMN document_governance JSONB NOT NULL DEFAULT '{}'::jsonb;

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_document_status_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_document_status_ck CHECK (
            document_status IN (
              'pre_formal','formal',
              'internal_review','delivery_candidate','approved_signed'
            )
          );

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_strategy_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_strategy_ck CHECK (
            candidate_group_strategy IN (
              'evidence_completeness_v1','preregistered_scope_v1'
            )
          );

        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_ck;
        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_check;
        ALTER TABLE reporting.report_artifact
          ADD CONSTRAINT report_artifact_format_ck
          CHECK (format IN ('docx','pdf','xlsx','html','manifest','zip'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM reporting.report_artifact WHERE format='zip';
        ALTER TABLE reporting.report_artifact
          DROP CONSTRAINT IF EXISTS report_artifact_format_ck;
        ALTER TABLE reporting.report_artifact
          ADD CONSTRAINT report_artifact_format_ck
          CHECK (format IN ('docx','pdf','xlsx','html','manifest'));

        UPDATE reporting.formal_report_production
        SET document_status=CASE
          WHEN document_status='internal_review' THEN 'pre_formal'
          ELSE 'formal'
        END
        WHERE document_status IN ('internal_review','delivery_candidate','approved_signed');
        UPDATE reporting.formal_report_production
        SET candidate_group_strategy='evidence_completeness_v1'
        WHERE candidate_group_strategy='preregistered_scope_v1';

        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_document_status_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_document_status_ck
          CHECK (document_status IN ('pre_formal','formal'));
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT formal_strategy_ck;
        ALTER TABLE reporting.formal_report_production
          ADD CONSTRAINT formal_strategy_ck
          CHECK (candidate_group_strategy='evidence_completeness_v1');
        ALTER TABLE reporting.formal_report_production DROP COLUMN document_governance;
        """
    )
