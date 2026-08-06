"""GEO source-article SOP workflow plane (session S05).

Creates the ``sop`` schema covering the full source-article loop defined by
《GEO信源型文章通用写作与验证流程 SOP》:
project definition (stage 0), frozen query sets (1), baseline answers (2),
retrieval insights (3), evidence ledger (4), content opportunities (5-6),
articles and versions (7), pre-publish checks (8), publications (9),
index observations (10), retest answers (11), comparisons (12-13),
experiments (14) and the append-only work log (15).

All tables carry ``tenant_pub_id`` and are placed under FORCE ROW LEVEL
SECURITY with the standard ``tenant_isolation`` policy, mirroring
``s04_0007`` / ``s04_0009``.

Revision ID: s05_0001
Revises: s04_0029
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s05_0001"
down_revision: str | Sequence[str] | None = "s04_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "project",
    "query_set",
    "query_item",
    "baseline_answer",
    "retrieval_insight",
    "evidence_item",
    "opportunity",
    "article",
    "article_version",
    "pre_publish_check",
    "publication",
    "index_observation",
    "retest_answer",
    "comparison",
    "experiment",
    "work_log",
)


def _force_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE sop.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE sop.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON sop.{table}
        USING (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        WITH CHECK (tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), ''))
        """
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA sop")

    # -- Stage 0: project definition --------------------------------------
    op.execute(
        """
        CREATE TABLE sop.project (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          name TEXT NOT NULL,
          brand_standard_name TEXT NOT NULL,
          brand_profile JSONB NOT NULL DEFAULT '{}',
          target_platforms JSONB NOT NULL DEFAULT '[]',
          success_definition JSONB NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
          created_by_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 1: frozen query sets ---------------------------------------
    op.execute(
        """
        CREATE TABLE sop.query_set (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          version_no INTEGER NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','frozen','superseded')),
          frozen_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, project_pub_id, version_no)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sop.query_item (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          query_set_pub_id TEXT NOT NULL REFERENCES sop.query_set(pub_id),
          ordinal INTEGER NOT NULL,
          query_text TEXT NOT NULL,
          layer TEXT NOT NULL CHECK (layer IN ('A','B','C','D','E','F','G')),
          contains_brand BOOLEAN NOT NULL DEFAULT false,
          intent TEXT NOT NULL DEFAULT '',
          persona TEXT NOT NULL DEFAULT '',
          decision_stage TEXT NOT NULL DEFAULT '',
          expected_facts TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'P1' CHECK (priority IN ('P0','P1','P2')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, query_set_pub_id, ordinal)
        )
        """
    )

    # -- Stage 2: baseline answers ----------------------------------------
    op.execute(
        """
        CREATE TABLE sop.baseline_answer (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          query_item_pub_id TEXT NOT NULL REFERENCES sop.query_item(pub_id),
          sample_index INTEGER NOT NULL DEFAULT 1,
          platform TEXT NOT NULL,
          region TEXT NOT NULL DEFAULT '',
          account_label TEXT NOT NULL DEFAULT '',
          mode TEXT NOT NULL DEFAULT '',
          asked_at TIMESTAMPTZ NOT NULL,
          capture_status TEXT NOT NULL
            CHECK (capture_status IN (
              'success','captcha','login_wall','interrupted','incomplete',
              'risk_control','search_disabled','sources_unloaded'
            )),
          answer_text TEXT NOT NULL DEFAULT '',
          reasoning_summary TEXT NOT NULL DEFAULT '',
          search_terms JSONB NOT NULL DEFAULT '[]',
          search_results JSONB NOT NULL DEFAULT '[]',
          citations JSONB NOT NULL DEFAULT '[]',
          brand_mentioned BOOLEAN,
          mention_context TEXT NOT NULL DEFAULT '',
          key_facts JSONB NOT NULL DEFAULT '[]',
          evidence_ref TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, query_item_pub_id, sample_index)
        )
        """
    )

    # -- Stage 3: retrieval insights --------------------------------------
    op.execute(
        """
        CREATE TABLE sop.retrieval_insight (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          insight_type TEXT NOT NULL
            CHECK (insight_type IN (
              'query_rewrite','source_selection','answer_usage','statistics','note'
            )),
          payload JSONB NOT NULL DEFAULT '{}',
          note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 4: evidence ledger -----------------------------------------
    op.execute(
        """
        CREATE TABLE sop.evidence_item (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          claim_text TEXT NOT NULL,
          source_name TEXT NOT NULL DEFAULT '',
          source_url TEXT NOT NULL DEFAULT '',
          source_level TEXT NOT NULL
            CHECK (source_level IN ('official','third_party','experience')),
          verified_at TIMESTAMPTZ,
          can_prove TEXT NOT NULL DEFAULT '',
          cannot_prove TEXT NOT NULL DEFAULT '',
          allowed_public BOOLEAN NOT NULL DEFAULT false,
          evidence_ref TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stages 5-6: content opportunities and source choice --------------
    op.execute(
        """
        CREATE TABLE sop.opportunity (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          target_query TEXT NOT NULL,
          current_gap TEXT NOT NULL DEFAULT '',
          current_sources JSONB NOT NULL DEFAULT '[]',
          brand_material TEXT NOT NULL DEFAULT '',
          needed_evidence TEXT NOT NULL DEFAULT '',
          recommended_platform TEXT NOT NULL DEFAULT '',
          expected_change TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'candidate'
            CHECK (status IN ('candidate','selected','rejected','fulfilled')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 7: articles and versions ------------------------------------
    op.execute(
        """
        CREATE TABLE sop.article (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          opportunity_pub_id TEXT REFERENCES sop.opportunity(pub_id),
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','in_review','ready','published','archived')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE sop.article_version (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          article_pub_id TEXT NOT NULL REFERENCES sop.article(pub_id),
          version_no INTEGER NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
          change_note TEXT NOT NULL DEFAULT '',
          readiness_checklist JSONB NOT NULL DEFAULT '{}',
          publication_ready BOOLEAN NOT NULL DEFAULT false,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, article_pub_id, version_no)
        )
        """
    )

    # -- Stage 8: pre-publish checks ---------------------------------------
    op.execute(
        """
        CREATE TABLE sop.pre_publish_check (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          article_version_pub_id TEXT NOT NULL REFERENCES sop.article_version(pub_id),
          check_type TEXT NOT NULL
            CHECK (check_type IN (
              'ai_dialogue','fact_verification','readability','extractability',
              'title_match','entity_disambiguation','source_completeness',
              'keyword_stuffing','compliance','rag_recall','synonym_test','other'
            )),
          result TEXT NOT NULL CHECK (result IN ('pass','warn','fail')),
          findings TEXT NOT NULL DEFAULT '',
          checked_by TEXT NOT NULL DEFAULT '',
          checked_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 9: publications ---------------------------------------------
    op.execute(
        """
        CREATE TABLE sop.publication (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          article_version_pub_id TEXT NOT NULL REFERENCES sop.article_version(pub_id),
          platform TEXT NOT NULL,
          account_label TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
          status TEXT NOT NULL DEFAULT 'submitted'
            CHECK (status IN (
              'submitted','reviewing','published','public',
              'rejected','withdrawn','login_only'
            )),
          public_url TEXT NOT NULL DEFAULT '',
          content_id TEXT NOT NULL DEFAULT '',
          submitted_at TIMESTAMPTZ,
          published_at TIMESTAMPTZ,
          public_checked_at TIMESTAMPTZ,
          public_http_status INTEGER,
          evidence JSONB NOT NULL DEFAULT '{}',
          note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 10: index observations --------------------------------------
    op.execute(
        """
        CREATE TABLE sop.index_observation (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          publication_pub_id TEXT NOT NULL REFERENCES sop.publication(pub_id),
          checkpoint TEXT NOT NULL
            CHECK (checkpoint IN ('immediate','h24','d3','d7','d14','custom')),
          checkpoint_label TEXT NOT NULL DEFAULT '',
          observed_at TIMESTAMPTZ NOT NULL,
          page_accessible BOOLEAN,
          search_engine_indexed BOOLEAN,
          platform_search_visible BOOLEAN,
          ai_retrieved BOOLEAN,
          ai_cited BOOLEAN,
          note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, publication_pub_id, checkpoint, checkpoint_label)
        )
        """
    )

    # -- Stage 11: retest answers ------------------------------------------
    op.execute(
        """
        CREATE TABLE sop.retest_answer (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          publication_pub_id TEXT NOT NULL REFERENCES sop.publication(pub_id),
          query_item_pub_id TEXT NOT NULL REFERENCES sop.query_item(pub_id),
          sample_index INTEGER NOT NULL DEFAULT 1,
          platform TEXT NOT NULL,
          region TEXT NOT NULL DEFAULT '',
          account_label TEXT NOT NULL DEFAULT '',
          mode TEXT NOT NULL DEFAULT '',
          asked_at TIMESTAMPTZ NOT NULL,
          capture_status TEXT NOT NULL
            CHECK (capture_status IN (
              'success','captcha','login_wall','interrupted','incomplete',
              'risk_control','search_disabled','sources_unloaded'
            )),
          answer_text TEXT NOT NULL DEFAULT '',
          reasoning_summary TEXT NOT NULL DEFAULT '',
          search_terms JSONB NOT NULL DEFAULT '[]',
          search_results JSONB NOT NULL DEFAULT '[]',
          citations JSONB NOT NULL DEFAULT '[]',
          brand_mentioned BOOLEAN,
          mention_context TEXT NOT NULL DEFAULT '',
          key_facts JSONB NOT NULL DEFAULT '[]',
          article_appeared BOOLEAN,
          article_position INTEGER,
          article_cited BOOLEAN,
          citation_position INTEGER,
          brand_attribution_correct BOOLEAN,
          new_facts JSONB NOT NULL DEFAULT '[]',
          errors_introduced TEXT NOT NULL DEFAULT '',
          evidence_ref TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, publication_pub_id, query_item_pub_id, sample_index)
        )
        """
    )

    # -- Stages 12-13: comparisons and attribution -------------------------
    op.execute(
        """
        CREATE TABLE sop.comparison (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          publication_pub_id TEXT NOT NULL REFERENCES sop.publication(pub_id),
          query_item_pub_id TEXT NOT NULL REFERENCES sop.query_item(pub_id),
          baseline_answer_pub_id TEXT REFERENCES sop.baseline_answer(pub_id),
          retest_answer_pub_id TEXT REFERENCES sop.retest_answer(pub_id),
          metrics JSONB NOT NULL DEFAULT '{}',
          new_info_location TEXT NOT NULL DEFAULT '',
          from_article_confidence TEXT NOT NULL DEFAULT 'none'
            CHECK (from_article_confidence IN ('high','medium','low','none')),
          attribution_correct BOOLEAN,
          conclusion TEXT NOT NULL DEFAULT '',
          next_actions JSONB NOT NULL DEFAULT '[]',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_pub_id, publication_pub_id, query_item_pub_id)
        )
        """
    )

    # -- Stage 14: experiments ---------------------------------------------
    op.execute(
        """
        CREATE TABLE sop.experiment (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          hypothesis TEXT NOT NULL,
          change_description TEXT NOT NULL DEFAULT '',
          controlled_conditions JSONB NOT NULL DEFAULT '{}',
          query_set_pub_id TEXT REFERENCES sop.query_set(pub_id),
          observation_window TEXT NOT NULL DEFAULT '',
          result TEXT NOT NULL DEFAULT '',
          next_step TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'planned'
            CHECK (status IN ('planned','running','done','abandoned')),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Stage 15: append-only work log ------------------------------------
    op.execute(
        """
        CREATE TABLE sop.work_log (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL REFERENCES sop.project(pub_id),
          entry_type TEXT NOT NULL
            CHECK (entry_type IN ('progress','failure','blocker','decision','note')),
          failure_class TEXT
            CHECK (failure_class IN (
              'captcha','login_wall','no_retrieval','sources_unloaded',
              'not_public','not_indexed','not_cited','wrong_attribution',
              'over_extrapolation','other'
            )),
          content TEXT NOT NULL,
          actor_pub_id TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- Indexes ------------------------------------------------------------
    op.execute(
        "CREATE INDEX query_set_project_idx ON sop.query_set (tenant_pub_id, project_pub_id)"
    )
    op.execute(
        "CREATE INDEX query_item_set_idx ON sop.query_item (tenant_pub_id, query_set_pub_id)"
    )
    op.execute(
        "CREATE INDEX baseline_project_idx ON sop.baseline_answer (tenant_pub_id, project_pub_id)"
    )
    op.execute(
        "CREATE INDEX baseline_query_item_idx ON sop.baseline_answer "
        "(tenant_pub_id, query_item_pub_id)"
    )
    op.execute(
        "CREATE INDEX retrieval_insight_project_idx ON sop.retrieval_insight "
        "(tenant_pub_id, project_pub_id)"
    )
    op.execute(
        "CREATE INDEX evidence_item_project_idx ON sop.evidence_item "
        "(tenant_pub_id, project_pub_id)"
    )
    op.execute(
        "CREATE INDEX opportunity_project_idx ON sop.opportunity (tenant_pub_id, project_pub_id)"
    )
    op.execute("CREATE INDEX article_project_idx ON sop.article (tenant_pub_id, project_pub_id)")
    op.execute(
        "CREATE INDEX article_version_article_idx ON sop.article_version "
        "(tenant_pub_id, article_pub_id)"
    )
    op.execute(
        "CREATE INDEX pre_publish_check_version_idx "
        "ON sop.pre_publish_check (tenant_pub_id, article_version_pub_id)"
    )
    op.execute(
        "CREATE INDEX publication_project_idx ON sop.publication (tenant_pub_id, project_pub_id)"
    )
    op.execute(
        "CREATE INDEX index_observation_publication_idx "
        "ON sop.index_observation (tenant_pub_id, publication_pub_id)"
    )
    op.execute(
        "CREATE INDEX retest_answer_publication_idx "
        "ON sop.retest_answer (tenant_pub_id, publication_pub_id)"
    )
    op.execute(
        "CREATE INDEX comparison_publication_idx ON sop.comparison "
        "(tenant_pub_id, publication_pub_id)"
    )
    op.execute(
        "CREATE INDEX experiment_project_idx ON sop.experiment (tenant_pub_id, project_pub_id)"
    )
    op.execute("CREATE INDEX work_log_project_idx ON sop.work_log (tenant_pub_id, project_pub_id)")

    for table in _TENANT_TABLES:
        _force_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON sop.{table}")
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS sop.{table}")
    op.execute("DROP SCHEMA IF EXISTS sop")
