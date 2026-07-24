# ruff: noqa: E501
"""S02 intelligence, evidence and reporting plane.

Revision ID: s02_0001
Revises: s00_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s02_0001"
down_revision: str | None = "s00_0001"
branch_labels: str | Sequence[str] | None = ("s02",)
depends_on: str | Sequence[str] | None = None

_DDL = r"""
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS evidence;
CREATE SCHEMA IF NOT EXISTS reporting;
CREATE SCHEMA IF NOT EXISTS intelligence;
CREATE SCHEMA IF NOT EXISTS integration;

CREATE TABLE analytics.analysis_run (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  scorer_version TEXT NOT NULL,
  metric_version TEXT NOT NULL,
  model_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','running','ready','failed')),
  advisory BOOLEAN NOT NULL DEFAULT FALSE,
  confidence NUMERIC(7,6),
  failure_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, input_hash, scorer_version, metric_version, model_version)
);

CREATE TABLE analytics.answer_analysis (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  answer_pub_id TEXT NOT NULL,
  analysis_run_pub_id TEXT NOT NULL REFERENCES analytics.analysis_run(pub_id),
  mentioned BOOLEAN NOT NULL,
  rank INTEGER CHECK (rank IS NULL OR rank > 0),
  sentiment TEXT CHECK (sentiment IS NULL OR sentiment IN ('positive','neutral','negative','unknown')),
  recommended BOOLEAN,
  recommendation_state TEXT NOT NULL DEFAULT 'experimental',
  competitor_ranks JSONB NOT NULL DEFAULT '{}',
  feature_payload JSONB NOT NULL DEFAULT '{}',
  platform_account_pub_id TEXT,
  browser_profile_version_pub_id TEXT,
  session_event_pub_id TEXT,
  channel TEXT NOT NULL CHECK (channel IN ('api','web')),
  authorization_scope TEXT[] NOT NULL DEFAULT '{}',
  adapter_version TEXT NOT NULL,
  capture_time TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, answer_pub_id, analysis_run_pub_id)
);

CREATE TABLE analytics.citation_fact (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  answer_pub_id TEXT NOT NULL,
  analysis_run_pub_id TEXT NOT NULL REFERENCES analytics.analysis_run(pub_id),
  ordinal INTEGER NOT NULL,
  original_url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  host TEXT NOT NULL,
  title TEXT,
  cited_text TEXT,
  own_source BOOLEAN NOT NULL DEFAULT FALSE,
  content_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, answer_pub_id, ordinal, analysis_run_pub_id)
);

CREATE TABLE analytics.metric_definition (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  experimental BOOLEAN NOT NULL DEFAULT FALSE,
  definition JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (name, version)
);

CREATE TABLE analytics.metric_daily (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  project_pub_id TEXT NOT NULL,
  metric_date DATE NOT NULL,
  metric_name TEXT NOT NULL,
  dimensions JSONB NOT NULL,
  dimensions_hash TEXT NOT NULL,
  value NUMERIC,
  numerator BIGINT,
  denominator BIGINT NOT NULL,
  state TEXT NOT NULL,
  metric_version TEXT NOT NULL,
  scorer_version TEXT NOT NULL,
  trace_token TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, project_pub_id, metric_date, metric_name, dimensions_hash,
          metric_version, scorer_version)
);

CREATE TABLE evidence.evidence_asset (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  project_pub_id TEXT,
  kind TEXT NOT NULL,
  access_class TEXT NOT NULL CHECK (access_class IN ('public','customer_private','paid_or_organization')),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key TEXT NOT NULL UNIQUE,
  mime_type TEXT NOT NULL,
  byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
  source_url TEXT,
  dlp_findings TEXT[] NOT NULL DEFAULT '{}',
  platform_account_pub_id TEXT,
  browser_profile_version_pub_id TEXT,
  session_event_pub_id TEXT,
  channel TEXT NOT NULL CHECK (channel IN ('api','web')),
  authorization_scope TEXT[] NOT NULL DEFAULT '{}',
  adapter_version TEXT NOT NULL,
  capture_time TIMESTAMPTZ NOT NULL,
  authorized_session_capture BOOLEAN NOT NULL DEFAULT FALSE,
  retention_until TIMESTAMPTZ,
  legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, sha256, kind)
);

CREATE TABLE evidence.evidence_anchor (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  text_start INTEGER,
  text_end INTEGER,
  bbox JSONB,
  page_number INTEGER,
  quote_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((text_start IS NULL AND text_end IS NULL) OR
         (text_start >= 0 AND text_end IS NOT NULL AND text_end > text_start))
);

CREATE TABLE evidence.evidence_relation (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  from_pub_id TEXT NOT NULL,
  to_pub_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, from_pub_id, to_pub_id, relation_type)
);

CREATE TABLE evidence.evidence_diff (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  before_evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  after_evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  text_diff JSONB,
  visual_diff_object_key TEXT,
  similarity NUMERIC(7,6),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, before_evidence_pub_id, after_evidence_pub_id)
);

CREATE TABLE evidence.evidence_package (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  object_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','ready','revoked','expired')),
  access_class TEXT NOT NULL,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  published_report_pub_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence.evidence_access_grant (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  package_pub_id TEXT NOT NULL REFERENCES evidence.evidence_package(pub_id),
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence.evidence_access_audit (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  resource_pub_id TEXT NOT NULL,
  actor_pub_id TEXT,
  action TEXT NOT NULL,
  outcome TEXT NOT NULL,
  request_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  data JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE reporting.report (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  project_pub_id TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','review','approved','published','superseded')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reporting.report_version (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_pub_id TEXT NOT NULL REFERENCES reporting.report(pub_id),
  version_number INTEGER NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  filters JSONB NOT NULL,
  filter_hash TEXT NOT NULL,
  metric_version TEXT NOT NULL,
  scorer_version TEXT NOT NULL,
  fact_snapshot_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  ai_draft_hash TEXT,
  human_edit_hash TEXT,
  created_by_pub_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, report_pub_id, version_number)
);

CREATE TABLE reporting.report_component (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
  component_type TEXT NOT NULL CHECK (component_type IN ('kpi','chart','section','evidence','recommendation')),
  ordinal INTEGER NOT NULL,
  payload JSONB NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('system','ai','human')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, report_version_pub_id, component_type, ordinal)
);

CREATE TABLE reporting.report_artifact (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_version_pub_id TEXT NOT NULL REFERENCES reporting.report_version(pub_id),
  format TEXT NOT NULL CHECK (format IN ('docx','pdf','xlsx','html')),
  evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, report_version_pub_id, format)
);

CREATE TABLE reporting.report_event (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_pub_id TEXT NOT NULL REFERENCES reporting.report(pub_id),
  report_version_pub_id TEXT,
  event_type TEXT NOT NULL,
  actor_pub_id TEXT NOT NULL,
  data JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reporting.optimization_action (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  report_pub_id TEXT NOT NULL REFERENCES reporting.report(pub_id),
  description TEXT NOT NULL,
  owner_pub_id TEXT,
  state TEXT NOT NULL CHECK (state IN ('proposed','accepted','in_progress','done','rejected')),
  baseline JSONB,
  outcome JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.investigation (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','collecting','review','decided','appealed','corrected')),
  access_class TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.content_item (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  canonical_url TEXT NOT NULL,
  content_type TEXT NOT NULL,
  author_pub_id TEXT,
  domain_pub_id TEXT,
  access_class TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, investigation_pub_id, canonical_url)
);

CREATE TABLE intelligence.content_version (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  content_pub_id TEXT NOT NULL REFERENCES intelligence.content_item(pub_id),
  version_number INTEGER NOT NULL,
  body_hash TEXT NOT NULL,
  title TEXT,
  body_text TEXT NOT NULL,
  search_vector TSVECTOR GENERATED ALWAYS AS
    (setweight(to_tsvector('simple', coalesce(title,'')), 'A') ||
     setweight(to_tsvector('simple', coalesce(body_text,'')), 'B')) STORED,
  embedding REAL[],
  evidence_pub_id TEXT REFERENCES evidence.evidence_asset(pub_id),
  published_at TIMESTAMPTZ,
  captured_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, content_pub_id, version_number),
  UNIQUE (tenant_pub_id, body_hash)
);
CREATE INDEX content_version_fts_idx ON intelligence.content_version USING GIN(search_vector);

CREATE TABLE intelligence.entity (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.claim (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  normalized_text TEXT NOT NULL,
  subject_entity_pub_id TEXT,
  predicate TEXT,
  object_text TEXT,
  verifiability TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.claim_occurrence (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  claim_pub_id TEXT NOT NULL REFERENCES intelligence.claim(pub_id),
  content_version_pub_id TEXT NOT NULL REFERENCES intelligence.content_version(pub_id),
  text_start INTEGER NOT NULL,
  text_end INTEGER NOT NULL,
  quote_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, claim_pub_id, content_version_pub_id, text_start)
);

CREATE TABLE intelligence.claim_evidence (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  claim_pub_id TEXT NOT NULL REFERENCES intelligence.claim(pub_id),
  evidence_pub_id TEXT NOT NULL REFERENCES evidence.evidence_asset(pub_id),
  relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts','insufficient')),
  source_cluster TEXT NOT NULL,
  independence_weight NUMERIC(7,6) NOT NULL,
  rationale TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, claim_pub_id, evidence_pub_id)
);

CREATE TABLE intelligence.graph_edge (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  from_pub_id TEXT NOT NULL,
  to_pub_id TEXT NOT NULL,
  relation TEXT NOT NULL CHECK (relation IN
    ('supports','contradicts','insufficient','derived_from','near_duplicate',
     'published_by','cites','mentions')),
  weight NUMERIC(7,6),
  evidence_pub_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_pub_id, from_pub_id, to_pub_id, relation)
);

CREATE TABLE intelligence.detection_score (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  probability NUMERIC(7,6) NOT NULL CHECK (probability BETWEEN 0 AND 1),
  evidence_sufficiency NUMERIC(7,6) NOT NULL CHECK (evidence_sufficiency BETWEEN 0 AND 1),
  independent_source_count INTEGER NOT NULL,
  uncertainty NUMERIC(7,6) NOT NULL CHECK (uncertainty BETWEEN 0 AND 1),
  rule_version TEXT NOT NULL,
  model_version TEXT NOT NULL,
  explanation JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.human_verdict (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  verdict TEXT NOT NULL CHECK (verdict IN ('likely','unlikely','uncertain','insufficient')),
  reviewer_pub_id TEXT NOT NULL,
  rationale TEXT NOT NULL,
  supersedes_pub_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intelligence.appeal (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pub_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  investigation_pub_id TEXT NOT NULL REFERENCES intelligence.investigation(pub_id),
  state TEXT NOT NULL CHECK (state IN ('open','reviewing','upheld','corrected','rejected')),
  submitted_by_pub_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  resolution TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integration.outbox_event (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  tenant_pub_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  aggregate_pub_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  published_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE integration.consumer_receipt (
  consumer_name TEXT NOT NULL,
  event_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  consumed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer_name, event_id)
);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    for schema in ("integration", "intelligence", "reporting", "evidence", "analytics"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
