"""Complete S02 evidence/report/intelligence domain models.

Revision ID: s02_0002
Revises: s02_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0002"
down_revision: str | None = "s02_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
CREATE TABLE analytics.metric_trace (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  trace_token TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  answer_pub_id TEXT NOT NULL,
  contribution JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id,trace_token,answer_pub_id)
);

CREATE TABLE analytics.anomaly_event (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  project_pub_id TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  detected_at TIMESTAMPTZ NOT NULL,
  severity TEXT NOT NULL,
  expected_value NUMERIC,
  observed_value NUMERIC,
  root_causes JSONB NOT NULL,
  trace_token TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence.evidence_snapshot (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  subject_pub_id TEXT NOT NULL,
  evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  snapshot_number INTEGER NOT NULL,
  normalized_text_hash TEXT,
  perceptual_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id,subject_pub_id,snapshot_number)
);

CREATE TABLE reporting.report_review (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
  reviewer_pub_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','changes_requested','rejected')),
  rationale TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reporting.report_comment (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
  parent_pub_id TEXT,
  author_pub_id TEXT NOT NULL,
  body TEXT NOT NULL,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reporting.report_delivery (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_pub_id TEXT NOT NULL REFERENCES reporting.report(pub_id),
  recipient_pub_id TEXT NOT NULL,
  delivered_at TIMESTAMPTZ NOT NULL,
  confirmed_at TIMESTAMPTZ,
  confirmation_comment TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.author_identity (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  opaque_author_id TEXT NOT NULL,
  display_name_hash TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  attributes JSONB NOT NULL DEFAULT '{}',
  UNIQUE (tenant_pub_id,platform,opaque_author_id)
);

CREATE TABLE intelligence.domain_profile (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  host TEXT NOT NULL,
  ownership_cluster TEXT,
  authority_class TEXT,
  first_seen_at TIMESTAMPTZ,
  attributes JSONB NOT NULL DEFAULT '{}',
  UNIQUE (tenant_pub_id,host)
);

CREATE TABLE intelligence.source_independence (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  source_pub_id TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  independence_weight NUMERIC(7,6) NOT NULL,
  circular_citation_risk NUMERIC(7,6) NOT NULL,
  reasons JSONB NOT NULL,
  rule_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id,investigation_pub_id,source_pub_id)
);

CREATE TABLE intelligence.similarity_edge (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  left_content_version_pub_id TEXT NOT NULL REFERENCES intelligence.content_version(pub_id),
  right_content_version_pub_id TEXT NOT NULL REFERENCES intelligence.content_version(pub_id),
  body_hash_equal BOOLEAN NOT NULL,
  semantic_similarity NUMERIC(7,6),
  same_source_cluster BOOLEAN NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id,left_content_version_pub_id,right_content_version_pub_id)
);

CREATE TABLE intelligence.propagation_event (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  content_version_pub_id TEXT NOT NULL REFERENCES intelligence.content_version(pub_id),
  source_cluster TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  published_at TIMESTAMPTZ,
  derived_from_pub_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.detection_feature (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  subject_pub_id TEXT NOT NULL,
  feature_family TEXT NOT NULL CHECK
    (feature_family IN ('content','source','propagation','external_fact')),
  feature_name TEXT NOT NULL,
  feature_value NUMERIC NOT NULL,
  explanation TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id,subject_pub_id,feature_name,rule_version)
);

CREATE OR REPLACE FUNCTION evidence.prevent_published_evidence_deletion()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL AND EXISTS (
    SELECT 1
    FROM reporting.report_artifact ra
    JOIN reporting.report_version rv ON rv.pub_id=ra.report_version_pub_id
    JOIN reporting.report r ON r.pub_id=rv.report_pub_id
    WHERE ra.evidence_pub_id=OLD.pub_id AND r.state='published'
  ) THEN
    RAISE EXCEPTION 'published report evidence is retained';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER retain_published_report_evidence
BEFORE UPDATE OF deleted_at ON evidence.evidence_asset
FOR EACH ROW EXECUTE FUNCTION evidence.prevent_published_evidence_deletion();

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='vector') THEN
    CREATE EXTENSION IF NOT EXISTS vector;
    ALTER TABLE intelligence.content_version
      ADD COLUMN IF NOT EXISTS embedding_vector vector;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS retain_published_report_evidence ON evidence.evidence_asset;
        DROP FUNCTION IF EXISTS evidence.prevent_published_evidence_deletion();
        DROP TABLE IF EXISTS intelligence.detection_feature;
        DROP TABLE IF EXISTS intelligence.propagation_event;
        DROP TABLE IF EXISTS intelligence.similarity_edge;
        DROP TABLE IF EXISTS intelligence.source_independence;
        DROP TABLE IF EXISTS intelligence.domain_profile;
        DROP TABLE IF EXISTS intelligence.author_identity;
        DROP TABLE IF EXISTS reporting.report_delivery;
        DROP TABLE IF EXISTS reporting.report_comment;
        DROP TABLE IF EXISTS reporting.report_review;
        DROP TABLE IF EXISTS evidence.evidence_snapshot;
        DROP TABLE IF EXISTS analytics.anomaly_event;
        DROP TABLE IF EXISTS analytics.metric_trace;
        """
    )
