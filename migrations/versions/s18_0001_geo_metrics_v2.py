# ruff: noqa: E501
"""Create the immutable GEO query-cohort metrics V2 fact plane.

Revision ID: s18_0001_geo_metrics_v2
Revises: s17_0005_credential_boundary
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.tenancy.runtime_acl import API_ROLE, WORKER_ROLE, migration_reconcile_sql

revision: str = "s18_0001_geo_metrics_v2"
down_revision: str | Sequence[str] | None = "s17_0005_credential_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HASH = r"^[0-9a-f]{64}$"

_TENANT_TABLES = (
    "query_context_fact_v2",
    "query_entity_exposure_fact_v2",
    "answer_semantic_manifest_v2",
    "answer_semantic_event_v2",
    "semantic_evidence_bundle_v2",
    "semantic_decision_job_v2",
    "semantic_decision_attempt_v2",
    "semantic_decision_record_v2",
    "metric_evaluation_v2",
    "metric_snapshot_set_v2",
    "metric_snapshot_v2",
    "metric_contribution_v2",
    "metric_query_contribution_v2",
    "metric_design_cell_contribution_v2",
    "metric_publication_v2",
    "metric_recompute_job_v2",
)

_APPEND_ONLY_TABLES = (
    "query_context_fact_v2",
    "query_entity_exposure_fact_v2",
    "answer_semantic_manifest_v2",
    "answer_semantic_event_v2",
    "semantic_evidence_bundle_v2",
    "semantic_decision_attempt_v2",
    "semantic_decision_record_v2",
    "metric_evaluation_v2",
    "metric_snapshot_set_v2",
    "metric_snapshot_v2",
    "metric_contribution_v2",
    "metric_query_contribution_v2",
    "metric_design_cell_contribution_v2",
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE analytics.semantic_decision_task_definition_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          name TEXT NOT NULL,
          version TEXT NOT NULL,
          subject_type TEXT NOT NULL,
          subject_ref_schema JSONB NOT NULL,
          business_question TEXT NOT NULL,
          input_schema JSONB NOT NULL,
          output_schema JSONB NOT NULL,
          dependency_task_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
          candidate_policy JSONB NOT NULL,
          decision_method_policy TEXT NOT NULL CHECK (
            decision_method_policy IN (
              'deterministic_only','model_required','hybrid','human_required'
            )
          ),
          rubric_ref TEXT NOT NULL,
          rubric_hash TEXT NOT NULL CHECK (rubric_hash ~ '{_HASH}'),
          prompt_template_ref TEXT NOT NULL,
          prompt_template_hash TEXT NOT NULL CHECK (prompt_template_hash ~ '{_HASH}'),
          evidence_requirements JSONB NOT NULL,
          abstention_policy JSONB NOT NULL,
          adjudication_policy JSONB NOT NULL,
          calibration_gate JSONB NOT NULL,
          definition_hash TEXT NOT NULL CHECK (definition_hash ~ '{_HASH}'),
          status TEXT NOT NULL CHECK (status IN ('draft','experimental','published','retired')),
          published_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_semantic_task_definition_name_version UNIQUE (name,version),
          CONSTRAINT uq_semantic_task_definition_hash UNIQUE (definition_hash),
          CONSTRAINT ck_semantic_task_definition_published_at CHECK (
            status <> 'published' OR published_at IS NOT NULL
          )
        );

        CREATE TABLE analytics.semantic_judge_policy_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          name TEXT NOT NULL,
          version TEXT NOT NULL,
          compatible_task_refs JSONB NOT NULL,
          method_pipeline JSONB NOT NULL,
          model_routes JSONB NOT NULL,
          inference_configs JSONB NOT NULL,
          timeout_retry_policy JSONB NOT NULL,
          acceptance_thresholds JSONB NOT NULL,
          disagreement_policy TEXT NOT NULL CHECK (
            disagreement_policy IN ('review','adjudicate','human_review')
          ),
          evidence_budget JSONB NOT NULL,
          cost_budget JSONB NOT NULL,
          fallback_policy JSONB NOT NULL,
          calibration_artifact_hash TEXT
            CHECK (calibration_artifact_hash IS NULL OR calibration_artifact_hash ~ '{_HASH}'),
          policy_hash TEXT NOT NULL CHECK (policy_hash ~ '{_HASH}'),
          status TEXT NOT NULL CHECK (status IN ('draft','experimental','published','retired')),
          published_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_semantic_judge_policy_name_version UNIQUE (name,version),
          CONSTRAINT uq_semantic_judge_policy_hash UNIQUE (policy_hash),
          CONSTRAINT ck_semantic_judge_policy_published CHECK (
            status <> 'published' OR (
              published_at IS NOT NULL AND calibration_artifact_hash IS NOT NULL
            )
          )
        );

        CREATE TABLE analytics.query_context_fact_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^qcf_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          query_pub_id TEXT,
          query_text_hash TEXT NOT NULL CHECK (query_text_hash ~ '{_HASH}'),
          primary_lens TEXT CHECK (
            primary_lens IS NULL OR primary_lens IN ('ai_impression','ai_recommendation')
          ),
          analysis_lenses TEXT[] NOT NULL,
          requested_operations TEXT[] NOT NULL,
          query_subtypes TEXT[] NOT NULL DEFAULT '{{}}',
          detected_entity_ids TEXT[] NOT NULL DEFAULT '{{}}',
          brand_structure_type TEXT NOT NULL CHECK (
            brand_structure_type IN (
              'brand_neutral','single_brand_named','multi_brand_named','unknown'
            )
          ),
          classification_state TEXT NOT NULL CHECK (
            classification_state IN ('ready','review_required','failed')
          ),
          classifier_version TEXT NOT NULL,
          decision_task_bundle_hash TEXT NOT NULL CHECK (decision_task_bundle_hash ~ '{_HASH}'),
          entity_dictionary_hash TEXT NOT NULL CHECK (entity_dictionary_hash ~ '{_HASH}'),
          classification_source TEXT NOT NULL CHECK (
            classification_source IN ('live','historical_backfill','manual_override')
          ),
          derivation_method TEXT NOT NULL CHECK (
            derivation_method IN ('deterministic','model','hybrid','human')
          ),
          decision_record_pub_ids TEXT[] NOT NULL,
          review_status TEXT NOT NULL CHECK (
            review_status IN ('unreviewed','approved','rejected','overridden')
          ),
          override_reason TEXT,
          supersedes_pub_id TEXT,
          fact_hash TEXT NOT NULL CHECK (fact_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_qcf_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_qcf_v2_identity UNIQUE (
            tenant_pub_id,project_pub_id,query_key,query_text_hash,classifier_version,
            decision_task_bundle_hash,entity_dictionary_hash,fact_hash
          ),
          CONSTRAINT uq_qcf_v2_superseded_once UNIQUE (tenant_pub_id,project_pub_id,supersedes_pub_id),
          CONSTRAINT fk_qcf_v2_supersedes FOREIGN KEY (
            tenant_pub_id,project_pub_id,supersedes_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_qcf_v2_ready_arrays CHECK (
            classification_state <> 'ready'
            OR (cardinality(analysis_lenses) > 0 AND cardinality(requested_operations) > 0)
          ),
          CONSTRAINT ck_qcf_v2_lenses CHECK (
            analysis_lenses <@ ARRAY['ai_impression','ai_recommendation']::text[]
          ),
          CONSTRAINT ck_qcf_v2_operations CHECK (
            requested_operations <@ ARRAY[
              'describe','fact_lookup','evaluate','recommend','compare','rank','explain'
            ]::text[]
          ),
          CONSTRAINT ck_qcf_v2_human_override CHECK (
            (derivation_method <> 'human' AND review_status <> 'overridden')
            OR nullif(btrim(override_reason),'') IS NOT NULL
          )
        );

        CREATE TABLE analytics.query_entity_exposure_fact_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^qef_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          query_context_fact_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          focal_entity_id TEXT NOT NULL,
          exposure_role TEXT NOT NULL CHECK (
            exposure_role IN (
              'brand_neutral','focal_named_only','focal_named_with_others',
              'other_brand_named','unknown'
            )
          ),
          matched_entity_ids TEXT[] NOT NULL DEFAULT '{{}}',
          fact_hash TEXT NOT NULL CHECK (fact_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_qef_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_qef_v2_entity UNIQUE (
            tenant_pub_id,query_context_fact_pub_id,focal_entity_id
          ),
          CONSTRAINT fk_qef_v2_context FOREIGN KEY (
            tenant_pub_id,project_pub_id,query_context_fact_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id)
        );

        CREATE TABLE analytics.answer_semantic_manifest_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^asm_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          answer_pub_id TEXT NOT NULL,
          analysis_run_pub_id TEXT NOT NULL,
          query_context_fact_pub_id TEXT NOT NULL,
          answer_text_hash TEXT NOT NULL CHECK (answer_text_hash ~ '{_HASH}'),
          input_hash TEXT NOT NULL CHECK (input_hash ~ '{_HASH}'),
          event_schema_version TEXT NOT NULL DEFAULT 'answer-semantic-events-v2'
            CHECK (event_schema_version = 'answer-semantic-events-v2'),
          extractor_bundle JSONB NOT NULL,
          decision_task_bundle JSONB NOT NULL,
          extractor_bundle_hash TEXT NOT NULL CHECK (extractor_bundle_hash ~ '{_HASH}'),
          decision_task_bundle_hash TEXT NOT NULL CHECK (decision_task_bundle_hash ~ '{_HASH}'),
          entity_dictionary_hash TEXT NOT NULL CHECK (entity_dictionary_hash ~ '{_HASH}'),
          status TEXT NOT NULL CHECK (status IN ('ready','partial','review_required','failed')),
          capability_statuses JSONB NOT NULL,
          decision_record_pub_ids TEXT[] NOT NULL DEFAULT '{{}}',
          decision_set_hash TEXT NOT NULL CHECK (decision_set_hash ~ '{_HASH}'),
          failure_code TEXT,
          failure_detail TEXT CHECK (failure_detail IS NULL OR char_length(failure_detail) <= 4000),
          event_count INTEGER NOT NULL CHECK (event_count >= 0),
          evidenced_event_count INTEGER NOT NULL CHECK (
            evidenced_event_count >= 0 AND evidenced_event_count <= event_count
          ),
          event_set_hash TEXT,
          supersedes_pub_id TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at TIMESTAMPTZ,
          CONSTRAINT uq_asm_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_asm_v2_identity UNIQUE (
            tenant_pub_id,answer_pub_id,input_hash,extractor_bundle_hash,
            decision_task_bundle_hash,entity_dictionary_hash
          ),
          CONSTRAINT uq_asm_v2_superseded_once UNIQUE (tenant_pub_id,project_pub_id,supersedes_pub_id),
          CONSTRAINT fk_asm_v2_context FOREIGN KEY (
            tenant_pub_id,project_pub_id,query_context_fact_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_asm_v2_answer FOREIGN KEY (tenant_pub_id,answer_pub_id)
            REFERENCES analytics.answer(tenant_pub_id,pub_id),
          CONSTRAINT fk_asm_v2_supersedes FOREIGN KEY (
            tenant_pub_id,project_pub_id,supersedes_pub_id
          ) REFERENCES analytics.answer_semantic_manifest_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_asm_v2_event_set CHECK (
            (status = 'failed' AND event_set_hash IS NULL)
            OR (status <> 'failed' AND event_set_hash ~ '{_HASH}')
          ),
          CONSTRAINT ck_asm_v2_completion CHECK (
            (status IN ('ready','partial','review_required') AND completed_at IS NOT NULL)
            OR status = 'failed'
          )
        );

        CREATE TABLE analytics.answer_semantic_event_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^ase_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          answer_pub_id TEXT NOT NULL,
          semantic_manifest_pub_id TEXT NOT NULL,
          event_index INTEGER NOT NULL CHECK (event_index >= 0),
          event_type TEXT NOT NULL CHECK (event_type IN (
            'entity_mention','recommendation_relation','sentiment_or_stance',
            'recommendation_list_rank','market_rank_claim','pairwise_preference',
            'mention_order','source_result_rank','factual_claim','claim_evidence_verdict',
            'citation_relation','risk_event'
          )),
          subject_entity_id TEXT,
          object_entity_id TEXT,
          event_value JSONB NOT NULL,
          qualifiers JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          answer_text_start INTEGER,
          answer_text_end INTEGER,
          offset_unit TEXT NOT NULL DEFAULT 'unicode_code_point_v1'
            CHECK (offset_unit = 'unicode_code_point_v1'),
          answer_excerpt_hash TEXT,
          extractor_version TEXT NOT NULL,
          scorer_version TEXT NOT NULL,
          derivation_method TEXT NOT NULL CHECK (
            derivation_method IN ('deterministic','model','hybrid','human')
          ),
          decision_record_pub_ids TEXT[] NOT NULL,
          decision_policy_version TEXT NOT NULL,
          provenance_hash TEXT NOT NULL CHECK (provenance_hash ~ '{_HASH}'),
          calibrated_confidence NUMERIC(20,12),
          confidence_state TEXT NOT NULL CHECK (
            confidence_state IN ('high','medium','low','unknown')
          ),
          review_status TEXT NOT NULL CHECK (
            review_status IN ('unreviewed','approved','rejected','overridden')
          ),
          override_reason TEXT,
          event_fingerprint TEXT NOT NULL CHECK (event_fingerprint ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_ase_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_ase_v2_fingerprint UNIQUE (
            tenant_pub_id,semantic_manifest_pub_id,event_fingerprint
          ),
          CONSTRAINT uq_ase_v2_event_index UNIQUE (
            tenant_pub_id,semantic_manifest_pub_id,event_index
          ),
          CONSTRAINT fk_ase_v2_manifest FOREIGN KEY (
            tenant_pub_id,project_pub_id,semantic_manifest_pub_id
          ) REFERENCES analytics.answer_semantic_manifest_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_ase_v2_offsets CHECK (
            (answer_text_start IS NULL AND answer_text_end IS NULL AND answer_excerpt_hash IS NULL)
            OR (answer_text_start >= 0 AND answer_text_end > answer_text_start
                AND answer_excerpt_hash ~ '{_HASH}')
          ),
          CONSTRAINT ck_ase_v2_confidence CHECK (
            calibrated_confidence IS NULL OR calibrated_confidence BETWEEN 0 AND 1
          ),
          CONSTRAINT ck_ase_v2_decisions CHECK (cardinality(decision_record_pub_ids) > 0),
          CONSTRAINT ck_ase_v2_human_override CHECK (
            (derivation_method <> 'human' AND review_status <> 'overridden')
            OR nullif(btrim(override_reason),'') IS NOT NULL
          )
        );

        CREATE TABLE analytics.semantic_evidence_bundle_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^seb_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          purpose_task_name TEXT NOT NULL,
          subject_key TEXT NOT NULL,
          truth_as_of_policy JSONB NOT NULL,
          verification_as_of TIMESTAMPTZ NOT NULL,
          retrieval_policy_hash TEXT NOT NULL CHECK (retrieval_policy_hash ~ '{_HASH}'),
          retrieval_query_hash TEXT NOT NULL CHECK (retrieval_query_hash ~ '{_HASH}'),
          source_items JSONB NOT NULL,
          source_count INTEGER NOT NULL CHECK (source_count >= 0),
          fetched_source_count INTEGER NOT NULL CHECK (
            fetched_source_count >= 0 AND fetched_source_count <= source_count
          ),
          status TEXT NOT NULL CHECK (status IN ('ready','partial','failed')),
          failure_codes TEXT[] NOT NULL DEFAULT '{{}}',
          bundle_hash TEXT NOT NULL CHECK (bundle_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_seb_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_seb_v2_bundle UNIQUE (tenant_pub_id,bundle_hash)
        );

        CREATE TABLE analytics.semantic_decision_job_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^sdj_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          task_name TEXT NOT NULL,
          task_version TEXT NOT NULL,
          task_definition_hash TEXT NOT NULL CHECK (task_definition_hash ~ '{_HASH}'),
          subject_type TEXT NOT NULL,
          subject_key TEXT NOT NULL,
          subject_ref JSONB NOT NULL,
          input_snapshot_ref TEXT NOT NULL,
          input_hash TEXT NOT NULL CHECK (input_hash ~ '{_HASH}'),
          context_hash TEXT NOT NULL CHECK (context_hash ~ '{_HASH}'),
          judge_policy_hash TEXT NOT NULL CHECK (judge_policy_hash ~ '{_HASH}'),
          rejudge_generation BIGINT NOT NULL DEFAULT 0 CHECK (rejudge_generation >= 0),
          supersedes_decision_pub_id TEXT,
          status TEXT NOT NULL CHECK (
            status IN ('pending','running','succeeded','abstained','review_required','failed')
          ),
          idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '{_HASH}'),
          selected_decision_pub_id TEXT,
          workflow_id TEXT,
          run_id TEXT,
          retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
          state_reason_codes TEXT[] NOT NULL DEFAULT '{{}}',
          failure_code TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          CONSTRAINT uq_sdj_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_sdj_v2_terminal_shape CHECK (
            (status IN ('pending','running','failed') AND selected_decision_pub_id IS NULL)
            OR (status IN ('succeeded','abstained','review_required')
                AND selected_decision_pub_id IS NOT NULL AND completed_at IS NOT NULL)
          )
        );

        CREATE TABLE analytics.semantic_decision_attempt_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^sda_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          decision_job_pub_id TEXT NOT NULL,
          attempt_index INTEGER NOT NULL CHECK (attempt_index >= 0),
          role TEXT NOT NULL CHECK (role IN ('proposer','verifier','adjudicator','human')),
          method TEXT NOT NULL CHECK (method IN ('deterministic','model','hybrid','human')),
          provider TEXT,
          model TEXT,
          model_revision TEXT,
          inference_config JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          prompt_template_ref TEXT NOT NULL,
          prompt_template_hash TEXT NOT NULL CHECK (prompt_template_hash ~ '{_HASH}'),
          rubric_hash TEXT NOT NULL CHECK (rubric_hash ~ '{_HASH}'),
          output_schema_hash TEXT NOT NULL CHECK (output_schema_hash ~ '{_HASH}'),
          request_payload_hash TEXT NOT NULL CHECK (request_payload_hash ~ '{_HASH}'),
          response_payload_hash TEXT CHECK (
            response_payload_hash IS NULL OR response_payload_hash ~ '{_HASH}'
          ),
          validated_output JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          rationale_summary TEXT CHECK (
            rationale_summary IS NULL OR char_length(rationale_summary) <= 4000
          ),
          validation_status TEXT NOT NULL,
          reason_codes TEXT[] NOT NULL DEFAULT '{{}}',
          latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
          input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
          output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
          cost_amount NUMERIC(20,12) CHECK (cost_amount IS NULL OR cost_amount >= 0),
          cost_currency TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_sda_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_sda_v2_attempt UNIQUE (
            tenant_pub_id,decision_job_pub_id,attempt_index,role
          ),
          CONSTRAINT fk_sda_v2_job FOREIGN KEY (
            tenant_pub_id,project_pub_id,decision_job_pub_id
          ) REFERENCES analytics.semantic_decision_job_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_sda_v2_model_route CHECK (
            method IN ('model','hybrid') OR (provider IS NULL AND model IS NULL AND model_revision IS NULL)
          )
        );

        CREATE TABLE analytics.semantic_decision_record_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^sdr_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          decision_job_pub_id TEXT NOT NULL,
          task_name TEXT NOT NULL,
          task_version TEXT NOT NULL,
          task_definition_hash TEXT NOT NULL CHECK (task_definition_hash ~ '{_HASH}'),
          subject_type TEXT NOT NULL,
          subject_key TEXT NOT NULL,
          subject_ref JSONB NOT NULL,
          metric_name TEXT,
          metric_version TEXT,
          input_snapshot_ref TEXT NOT NULL,
          input_hash TEXT NOT NULL CHECK (input_hash ~ '{_HASH}'),
          context_hash TEXT NOT NULL CHECK (context_hash ~ '{_HASH}'),
          method TEXT NOT NULL CHECK (method IN ('deterministic','model','hybrid','human')),
          status TEXT NOT NULL CHECK (
            status IN ('accepted','abstained','review_required','failed')
          ),
          result JSONB NOT NULL,
          rationale_summary TEXT CHECK (
            rationale_summary IS NULL OR char_length(rationale_summary) <= 4000
          ),
          calibrated_confidence NUMERIC(20,12),
          calibration_bucket TEXT,
          reason_codes TEXT[] NOT NULL,
          evidence_refs JSONB NOT NULL,
          evidence_spans JSONB NOT NULL,
          selected_attempt_pub_ids TEXT[] NOT NULL,
          judge_policy_hash TEXT NOT NULL CHECK (judge_policy_hash ~ '{_HASH}'),
          rubric_ref TEXT NOT NULL,
          rubric_hash TEXT NOT NULL CHECK (rubric_hash ~ '{_HASH}'),
          output_schema_hash TEXT NOT NULL CHECK (output_schema_hash ~ '{_HASH}'),
          supersedes_pub_id TEXT,
          decision_hash TEXT NOT NULL CHECK (decision_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_sdr_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_sdr_v2_job UNIQUE (tenant_pub_id,decision_job_pub_id),
          CONSTRAINT uq_sdr_v2_identity UNIQUE (
            tenant_pub_id,task_definition_hash,subject_type,subject_key,input_hash,
            context_hash,judge_policy_hash,decision_hash
          ),
          CONSTRAINT uq_sdr_v2_superseded_once UNIQUE (
            tenant_pub_id,project_pub_id,supersedes_pub_id
          ),
          CONSTRAINT fk_sdr_v2_job FOREIGN KEY (
            tenant_pub_id,project_pub_id,decision_job_pub_id
          ) REFERENCES analytics.semantic_decision_job_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_sdr_v2_supersedes FOREIGN KEY (
            tenant_pub_id,project_pub_id,supersedes_pub_id
          ) REFERENCES analytics.semantic_decision_record_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_sdr_v2_metric_pair CHECK (
            (metric_name IS NULL) = (metric_version IS NULL)
          ),
          CONSTRAINT ck_sdr_v2_confidence CHECK (
            calibrated_confidence IS NULL OR calibrated_confidence BETWEEN 0 AND 1
          ),
          CONSTRAINT ck_sdr_v2_attempts CHECK (
            status <> 'accepted' OR cardinality(selected_attempt_pub_ids) > 0
          )
        );

        ALTER TABLE analytics.semantic_decision_job_v2
          ADD CONSTRAINT fk_sdj_v2_selected_decision FOREIGN KEY (
            tenant_pub_id,project_pub_id,selected_decision_pub_id
          ) REFERENCES analytics.semantic_decision_record_v2(tenant_pub_id,project_pub_id,pub_id),
          ADD CONSTRAINT fk_sdj_v2_supersedes_decision FOREIGN KEY (
            tenant_pub_id,project_pub_id,supersedes_decision_pub_id
          ) REFERENCES analytics.semantic_decision_record_v2(tenant_pub_id,project_pub_id,pub_id);

        ALTER TABLE analytics.metric_definition
          ADD COLUMN definition_schema_version TEXT,
          ADD COLUMN definition_hash TEXT,
          ADD COLUMN status TEXT NOT NULL DEFAULT 'legacy'
            CHECK (status IN ('draft','experimental','published','retired','legacy')),
          ADD COLUMN unit_type TEXT CHECK (
            unit_type IS NULL OR unit_type IN (
              'answer','claim','relation','citation','dimension','design_cell'
            )
          ),
          ADD COLUMN required_event_types TEXT[] NOT NULL DEFAULT '{{}}',
          ADD COLUMN required_semantic_capabilities JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          ADD COLUMN decision_task_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
          ADD COLUMN outcome_source TEXT CHECK (
            outcome_source IS NULL OR outcome_source IN (
              'deterministic_expression','semantic_decision','hybrid'
            )
          ),
          ADD COLUMN semantic_rubric_ref TEXT,
          ADD COLUMN adjudication_uncertainty_policy JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          ADD COLUMN allowed_aggregation_methods TEXT[] NOT NULL DEFAULT '{{}}',
          ADD COLUMN default_aggregation_method TEXT,
          ADD COLUMN publication_gate JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          ADD COLUMN published_at TIMESTAMPTZ;
        CREATE UNIQUE INDEX uq_metric_definition_v2_hash
          ON analytics.metric_definition(definition_hash) WHERE definition_hash IS NOT NULL;
        ALTER TABLE analytics.metric_definition
          ADD CONSTRAINT ck_metric_definition_v2_hash CHECK (
            definition_hash IS NULL OR definition_hash ~ '{_HASH}'
          ),
          ADD CONSTRAINT ck_metric_definition_v2_published CHECK (
            status <> 'published' OR (
              definition_schema_version IS NOT NULL AND definition_hash IS NOT NULL
              AND unit_type IS NOT NULL AND outcome_source IS NOT NULL
              AND cardinality(allowed_aggregation_methods) > 0
              AND default_aggregation_method = ANY(allowed_aggregation_methods)
              AND published_at IS NOT NULL
            )
          );

        CREATE TABLE analytics.metric_evaluation_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mev_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          answer_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          focal_entity_id TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          metric_definition_hash TEXT NOT NULL CHECK (metric_definition_hash ~ '{_HASH}'),
          query_context_fact_pub_id TEXT NOT NULL,
          semantic_manifest_pub_id TEXT NOT NULL,
          semantic_decision_pub_ids TEXT[] NOT NULL DEFAULT '{{}}',
          semantic_decision_set_hash TEXT NOT NULL CHECK (semantic_decision_set_hash ~ '{_HASH}'),
          eligibility_status TEXT NOT NULL CHECK (eligibility_status IN (
            'included_hit','included_miss','excluded','not_applicable','analysis_unknown'
          )),
          reason_codes TEXT[] NOT NULL CHECK (cardinality(reason_codes) > 0),
          outcome_value JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          numerator_contribution NUMERIC(20,12),
          denominator_contribution NUMERIC(20,12),
          supporting_event_pub_ids TEXT[] NOT NULL DEFAULT '{{}}',
          evaluation_hash TEXT NOT NULL CHECK (evaluation_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mev_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mev_v2_identity UNIQUE (
            tenant_pub_id,answer_pub_id,focal_entity_id,metric_name,metric_version,
            query_context_fact_pub_id,semantic_manifest_pub_id,semantic_decision_set_hash
          ),
          CONSTRAINT fk_mev_v2_answer FOREIGN KEY (tenant_pub_id,answer_pub_id)
            REFERENCES analytics.answer(tenant_pub_id,pub_id),
          CONSTRAINT fk_mev_v2_context FOREIGN KEY (
            tenant_pub_id,project_pub_id,query_context_fact_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_mev_v2_manifest FOREIGN KEY (
            tenant_pub_id,project_pub_id,semantic_manifest_pub_id
          ) REFERENCES analytics.answer_semantic_manifest_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_mev_v2_contributions CHECK (
            (numerator_contribution IS NULL OR numerator_contribution >= 0)
            AND (denominator_contribution IS NULL OR denominator_contribution >= 0)
          )
        );

        CREATE TABLE analytics.metric_snapshot_set_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mss_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          window_start TIMESTAMPTZ NOT NULL,
          window_end TIMESTAMPTZ NOT NULL,
          as_of TIMESTAMPTZ NOT NULL,
          focal_entity_ids TEXT[] NOT NULL CHECK (cardinality(focal_entity_ids) > 0),
          filters JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          filter_hash TEXT NOT NULL CHECK (filter_hash ~ '{_HASH}'),
          scope_hash TEXT NOT NULL CHECK (scope_hash ~ '{_HASH}'),
          aggregation_method TEXT NOT NULL CHECK (aggregation_method = 'query_macro'),
          design_basis TEXT NOT NULL CHECK (design_basis IN ('planned_cells','observed_cells')),
          query_set_hash TEXT NOT NULL CHECK (query_set_hash ~ '{_HASH}'),
          design_set_hash TEXT NOT NULL CHECK (design_set_hash ~ '{_HASH}'),
          dependency_bundle JSONB NOT NULL,
          dependency_bundle_hash TEXT NOT NULL CHECK (dependency_bundle_hash ~ '{_HASH}'),
          state TEXT NOT NULL CHECK (state IN ('ready','partial','failed')),
          failure_codes TEXT[] NOT NULL DEFAULT '{{}}',
          snapshot_count INTEGER NOT NULL CHECK (snapshot_count >= 0),
          snapshot_set_hash TEXT NOT NULL CHECK (snapshot_set_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mss_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mss_v2_identity UNIQUE (
            tenant_pub_id,scope_hash,dependency_bundle_hash
          ),
          CONSTRAINT ck_mss_v2_window CHECK (window_start < window_end)
        );

        CREATE TABLE analytics.metric_snapshot_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^msn_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          snapshot_set_pub_id TEXT NOT NULL,
          focal_entity_id TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          metric_definition_hash TEXT NOT NULL CHECK (metric_definition_hash ~ '{_HASH}'),
          state TEXT NOT NULL CHECK (
            state IN ('ready','limited','insufficient','experimental','failed')
          ),
          state_reason_codes TEXT[] NOT NULL DEFAULT '{{}}',
          value NUMERIC(20,12),
          observed_value NUMERIC(20,12),
          answer_weighted_value NUMERIC(20,12),
          lower_bound NUMERIC(20,12),
          upper_bound NUMERIC(20,12),
          semantic_lower_bound NUMERIC(20,12),
          semantic_upper_bound NUMERIC(20,12),
          weighted_numerator NUMERIC(20,12) NOT NULL,
          weighted_denominator NUMERIC(20,12) NOT NULL,
          raw_numerator NUMERIC(20,12) NOT NULL,
          raw_denominator NUMERIC(20,12) NOT NULL,
          candidate_answer_count BIGINT NOT NULL CHECK (candidate_answer_count >= 0),
          known_answer_count BIGINT NOT NULL CHECK (known_answer_count >= 0),
          unknown_answer_count BIGINT NOT NULL CHECK (unknown_answer_count >= 0),
          decision_abstained_count BIGINT NOT NULL CHECK (decision_abstained_count >= 0),
          decision_review_required_count BIGINT NOT NULL
            CHECK (decision_review_required_count >= 0),
          not_applicable_answer_count BIGINT NOT NULL CHECK (not_applicable_answer_count >= 0),
          excluded_answer_count BIGINT NOT NULL CHECK (excluded_answer_count >= 0),
          unique_query_count BIGINT NOT NULL CHECK (unique_query_count >= 0),
          design_cell_count BIGINT NOT NULL CHECK (design_cell_count >= 0),
          effective_sample_size NUMERIC(20,12) NOT NULL CHECK (effective_sample_size >= 0),
          collection_coverage NUMERIC(20,12),
          query_context_coverage NUMERIC(20,12),
          semantic_coverage NUMERIC(20,12),
          evidence_coverage NUMERIC(20,12),
          semantic_coverage_by_capability JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          decision_method_mix JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          bootstrap_low NUMERIC(20,12),
          bootstrap_high NUMERIC(20,12),
          bootstrap_method TEXT,
          bootstrap_seed BIGINT,
          adjudication_sensitivity_low NUMERIC(20,12),
          adjudication_sensitivity_high NUMERIC(20,12),
          calibration_artifact_hashes TEXT[] NOT NULL DEFAULT '{{}}',
          contribution_set_hash TEXT NOT NULL CHECK (contribution_set_hash ~ '{_HASH}'),
          query_contribution_set_hash TEXT NOT NULL
            CHECK (query_contribution_set_hash ~ '{_HASH}'),
          design_contribution_set_hash TEXT NOT NULL
            CHECK (design_contribution_set_hash ~ '{_HASH}'),
          snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_msn_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_msn_v2_metric UNIQUE (
            tenant_pub_id,snapshot_set_pub_id,focal_entity_id,metric_name,metric_version
          ),
          CONSTRAINT fk_msn_v2_set FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_set_pub_id
          ) REFERENCES analytics.metric_snapshot_set_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_msn_v2_value_state CHECK (state <> 'failed' OR value IS NULL),
          CONSTRAINT ck_msn_v2_counts CHECK (
            known_answer_count + unknown_answer_count <= candidate_answer_count
            AND not_applicable_answer_count + excluded_answer_count <= candidate_answer_count
          ),
          CONSTRAINT ck_msn_v2_nonnegative_totals CHECK (
            weighted_numerator >= 0 AND weighted_denominator >= 0
            AND raw_numerator >= 0 AND raw_denominator >= 0
          ),
          CONSTRAINT ck_msn_v2_ratio_ranges CHECK (
            (value IS NULL OR value BETWEEN 0 AND 1)
            AND (observed_value IS NULL OR observed_value BETWEEN 0 AND 1)
            AND (answer_weighted_value IS NULL OR answer_weighted_value BETWEEN 0 AND 1)
            AND (lower_bound IS NULL OR lower_bound BETWEEN 0 AND 1)
            AND (upper_bound IS NULL OR upper_bound BETWEEN 0 AND 1)
            AND (semantic_lower_bound IS NULL OR semantic_lower_bound BETWEEN 0 AND 1)
            AND (semantic_upper_bound IS NULL OR semantic_upper_bound BETWEEN 0 AND 1)
            AND (collection_coverage IS NULL OR collection_coverage BETWEEN 0 AND 1)
            AND (query_context_coverage IS NULL OR query_context_coverage BETWEEN 0 AND 1)
            AND (semantic_coverage IS NULL OR semantic_coverage BETWEEN 0 AND 1)
            AND (evidence_coverage IS NULL OR evidence_coverage BETWEEN 0 AND 1)
            AND (bootstrap_low IS NULL OR bootstrap_low BETWEEN 0 AND 1)
            AND (bootstrap_high IS NULL OR bootstrap_high BETWEEN 0 AND 1)
            AND (adjudication_sensitivity_low IS NULL
                 OR adjudication_sensitivity_low BETWEEN 0 AND 1)
            AND (adjudication_sensitivity_high IS NULL
                 OR adjudication_sensitivity_high BETWEEN 0 AND 1)
          ),
          CONSTRAINT ck_msn_v2_bounds_order CHECK (
            (lower_bound IS NULL OR upper_bound IS NULL OR lower_bound <= upper_bound)
            AND (semantic_lower_bound IS NULL OR semantic_upper_bound IS NULL
                 OR semantic_lower_bound <= semantic_upper_bound)
            AND (bootstrap_low IS NULL OR bootstrap_high IS NULL OR bootstrap_low <= bootstrap_high)
            AND (adjudication_sensitivity_low IS NULL
                 OR adjudication_sensitivity_high IS NULL
                 OR adjudication_sensitivity_low <= adjudication_sensitivity_high)
          )
        );

        CREATE TABLE analytics.metric_contribution_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mct_.+'),
          snapshot_pub_id TEXT NOT NULL,
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          answer_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          focal_entity_id TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          model TEXT NOT NULL,
          region TEXT NOT NULL,
          mode TEXT NOT NULL,
          capture_time TIMESTAMPTZ NOT NULL,
          eligibility_status TEXT NOT NULL CHECK (eligibility_status IN (
            'included_hit','included_miss','excluded','not_applicable','analysis_unknown'
          )),
          reason_codes TEXT[] NOT NULL CHECK (cardinality(reason_codes) > 0),
          outcome_value JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          numerator_contribution NUMERIC(20,12),
          denominator_contribution NUMERIC(20,12),
          query_weight NUMERIC(20,12) NOT NULL CHECK (query_weight >= 0),
          design_cell_weight NUMERIC(20,12) NOT NULL CHECK (design_cell_weight >= 0),
          repeat_weight NUMERIC(20,12) NOT NULL CHECK (repeat_weight >= 0),
          final_weight NUMERIC(20,12) NOT NULL CHECK (final_weight >= 0),
          weighted_numerator NUMERIC(20,12) NOT NULL CHECK (weighted_numerator >= 0),
          weighted_denominator NUMERIC(20,12) NOT NULL CHECK (weighted_denominator >= 0),
          query_context_fact_pub_id TEXT NOT NULL,
          semantic_manifest_pub_id TEXT NOT NULL,
          supporting_event_pub_ids TEXT[] NOT NULL DEFAULT '{{}}',
          supporting_decision_pub_ids TEXT[] NOT NULL DEFAULT '{{}}',
          semantic_decision_set_hash TEXT NOT NULL CHECK (semantic_decision_set_hash ~ '{_HASH}'),
          dimension_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          answer_detail_ref TEXT NOT NULL,
          contribution_hash TEXT NOT NULL CHECK (contribution_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mct_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mct_v2_answer UNIQUE (tenant_pub_id,snapshot_pub_id,answer_pub_id),
          CONSTRAINT fk_mct_v2_snapshot FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_pub_id
          ) REFERENCES analytics.metric_snapshot_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_mct_v2_context FOREIGN KEY (
            tenant_pub_id,project_pub_id,query_context_fact_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_mct_v2_manifest FOREIGN KEY (
            tenant_pub_id,project_pub_id,semantic_manifest_pub_id
          ) REFERENCES analytics.answer_semantic_manifest_v2(tenant_pub_id,project_pub_id,pub_id)
        );

        CREATE TABLE analytics.metric_query_contribution_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mqc_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          snapshot_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          focal_entity_id TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          query_context_fact_pub_id TEXT NOT NULL,
          query_numerator NUMERIC(20,12) NOT NULL CHECK (query_numerator >= 0),
          query_denominator NUMERIC(20,12) NOT NULL CHECK (query_denominator >= 0),
          query_value NUMERIC(20,12),
          unknown_weight NUMERIC(20,12) NOT NULL CHECK (unknown_weight >= 0),
          query_weight NUMERIC(20,12) NOT NULL CHECK (query_weight >= 0),
          design_cell_count BIGINT NOT NULL CHECK (design_cell_count >= 0),
          answer_count BIGINT NOT NULL CHECK (answer_count >= 0),
          known_answer_count BIGINT NOT NULL CHECK (known_answer_count >= 0),
          unknown_answer_count BIGINT NOT NULL CHECK (unknown_answer_count >= 0),
          reason_codes TEXT[] NOT NULL DEFAULT '{{}}',
          contribution_hash TEXT NOT NULL CHECK (contribution_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mqc_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mqc_v2_query UNIQUE (tenant_pub_id,snapshot_pub_id,query_key),
          CONSTRAINT fk_mqc_v2_snapshot FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_pub_id
          ) REFERENCES analytics.metric_snapshot_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_mqc_v2_context FOREIGN KEY (
            tenant_pub_id,project_pub_id,query_context_fact_pub_id
          ) REFERENCES analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_mqc_v2_value CHECK (query_value IS NULL OR query_value BETWEEN 0 AND 1)
        );

        CREATE TABLE analytics.metric_design_cell_contribution_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mdc_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          snapshot_pub_id TEXT NOT NULL,
          query_key TEXT NOT NULL,
          model TEXT NOT NULL,
          region TEXT NOT NULL,
          mode TEXT NOT NULL,
          planned_repeat_count INTEGER NOT NULL CHECK (planned_repeat_count >= 0),
          valid_repeat_count INTEGER NOT NULL CHECK (valid_repeat_count >= 0),
          failed_repeat_count INTEGER NOT NULL CHECK (failed_repeat_count >= 0),
          known_repeat_count INTEGER NOT NULL CHECK (known_repeat_count >= 0),
          cell_weight NUMERIC(20,12) NOT NULL CHECK (cell_weight >= 0),
          state TEXT NOT NULL CHECK (state IN ('ready','partial','missing','failed','unknown')),
          reason_codes TEXT[] NOT NULL DEFAULT '{{}}',
          contribution_hash TEXT NOT NULL CHECK (contribution_hash ~ '{_HASH}'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mdc_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mdc_v2_cell UNIQUE (
            tenant_pub_id,snapshot_pub_id,query_key,model,region,mode
          ),
          CONSTRAINT fk_mdc_v2_snapshot FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_pub_id
          ) REFERENCES analytics.metric_snapshot_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_mdc_v2_counts CHECK (
            valid_repeat_count + failed_repeat_count <= planned_repeat_count
            AND known_repeat_count <= valid_repeat_count
          )
        );

        CREATE TABLE analytics.metric_publication_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mpu_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          scope_hash TEXT NOT NULL CHECK (scope_hash ~ '{_HASH}'),
          snapshot_set_pub_id TEXT NOT NULL,
          publication_channel TEXT NOT NULL CHECK (publication_channel IN ('shadow','official')),
          generation BIGINT NOT NULL CHECK (generation >= 0),
          published_by TEXT NOT NULL,
          published_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_mpu_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT uq_mpu_v2_pointer UNIQUE (tenant_pub_id,scope_hash,publication_channel),
          CONSTRAINT fk_mpu_v2_set FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_set_pub_id
          ) REFERENCES analytics.metric_snapshot_set_v2(tenant_pub_id,project_pub_id,pub_id)
        );

        CREATE TABLE analytics.metric_recompute_job_v2 (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          pub_id TEXT NOT NULL UNIQUE CHECK (pub_id ~ '^mrj_.+'),
          tenant_pub_id TEXT NOT NULL,
          project_pub_id TEXT NOT NULL,
          scope JSONB NOT NULL,
          scope_hash TEXT NOT NULL CHECK (scope_hash ~ '{_HASH}'),
          trigger_event_id TEXT,
          target_definition_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
          status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed')),
          cursor_state JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          input_count BIGINT NOT NULL DEFAULT 0 CHECK (input_count >= 0),
          output_count BIGINT NOT NULL DEFAULT 0 CHECK (output_count >= 0),
          skipped_count BIGINT NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
          failure_codes TEXT[] NOT NULL DEFAULT '{{}}',
          retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
          workflow_id TEXT,
          run_id TEXT,
          snapshot_set_pub_id TEXT,
          idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key ~ '{_HASH}'),
          requested_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,
          CONSTRAINT uq_mrj_v2_scope_pub UNIQUE (tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT fk_mrj_v2_set FOREIGN KEY (
            tenant_pub_id,project_pub_id,snapshot_set_pub_id
          ) REFERENCES analytics.metric_snapshot_set_v2(tenant_pub_id,project_pub_id,pub_id),
          CONSTRAINT ck_mrj_v2_terminal CHECK (
            status NOT IN ('succeeded','failed') OR completed_at IS NOT NULL
          )
        );

        ALTER TABLE reporting.data_export
          DROP CONSTRAINT IF EXISTS data_export_export_type_check;
        ALTER TABLE reporting.data_export
          ADD CONSTRAINT ck_data_export_type CHECK (
            export_type IN ('metric_xlsx','metric_v2_xlsx','metric_v2_csv_zip')
          );

        ALTER TABLE reporting.formal_report_production
          ADD COLUMN metric_snapshot_set_pub_id TEXT,
          ADD COLUMN metric_snapshot_set_hash TEXT,
          ADD COLUMN metric_snapshot_filters JSONB,
          ADD COLUMN metric_snapshot_dependency_hash TEXT,
          ADD CONSTRAINT formal_metric_snapshot_binding_ck CHECK (
            (
              metric_snapshot_set_pub_id IS NULL
              AND metric_snapshot_set_hash IS NULL
              AND metric_snapshot_filters IS NULL
              AND metric_snapshot_dependency_hash IS NULL
            ) OR (
              metric_snapshot_set_pub_id IS NOT NULL
              AND metric_snapshot_set_hash ~ '{_HASH}'
              AND metric_snapshot_filters IS NOT NULL
              AND metric_snapshot_dependency_hash ~ '{_HASH}'
            )
          ),
          ADD CONSTRAINT formal_metric_snapshot_set_fk FOREIGN KEY (
            tenant_pub_id,project_pub_id,metric_snapshot_set_pub_id
          ) REFERENCES analytics.metric_snapshot_set_v2(tenant_pub_id,project_pub_id,pub_id);

        CREATE VIEW analytics.semantic_decision_override_command_v2 AS
        SELECT
          NULL::TEXT AS tenant_pub_id,
          NULL::TEXT AS previous_decision_pub_id,
          NULL::JSONB AS result,
          NULL::TEXT AS rationale_summary,
          NULL::TEXT[] AS reason_codes,
          NULL::TEXT AS expected_decision_hash,
          NULL::TEXT AS actor_pub_id,
          NULL::TEXT AS decision_job_pub_id,
          NULL::TEXT AS new_decision_pub_id,
          NULL::TEXT AS new_decision_hash,
          NULL::TEXT AS recompute_job_pub_id
        WHERE false;

        CREATE FUNCTION analytics.metrics_v2_create_override_command()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog,analytics,public
        AS $$
        DECLARE
          previous analytics.semantic_decision_record_v2%ROWTYPE;
          previous_generation BIGINT;
          generation BIGINT;
          decision_hash TEXT;
          decision_idem TEXT;
          recompute_hash TEXT;
          job_pub_id TEXT;
          attempt_pub_id TEXT;
          next_decision_pub_id TEXT;
          recompute_pub_id TEXT;
          now_at TIMESTAMPTZ := clock_timestamp();
        BEGIN
          IF NEW.tenant_pub_id IS DISTINCT FROM
             current_setting('app.tenant_pub_id',true) THEN
            RAISE EXCEPTION 'metrics_v2_override_tenant_mismatch' USING ERRCODE='42501';
          END IF;
          IF nullif(btrim(NEW.actor_pub_id),'') IS NULL
             OR nullif(btrim(NEW.rationale_summary),'') IS NULL
             OR cardinality(NEW.reason_codes)=0 THEN
            RAISE EXCEPTION 'metrics_v2_override_input_invalid' USING ERRCODE='22023';
          END IF;
          SELECT * INTO previous
          FROM analytics.semantic_decision_record_v2
          WHERE tenant_pub_id=NEW.tenant_pub_id
            AND pub_id=NEW.previous_decision_pub_id
          FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'metrics_v2_semantic_decision_not_found'
              USING ERRCODE='P0002';
          END IF;
          IF previous.decision_hash IS DISTINCT FROM NEW.expected_decision_hash THEN
            RAISE EXCEPTION 'metrics_v2_decision_hash_conflict' USING ERRCODE='40001';
          END IF;
          PERFORM 1 FROM analytics.semantic_decision_record_v2
          WHERE tenant_pub_id=NEW.tenant_pub_id
            AND supersedes_pub_id=NEW.previous_decision_pub_id;
          IF FOUND THEN
            RAISE EXCEPTION 'metrics_v2_decision_already_superseded'
              USING ERRCODE='40001';
          END IF;
          SELECT rejudge_generation INTO previous_generation
          FROM analytics.semantic_decision_job_v2
          WHERE tenant_pub_id=previous.tenant_pub_id
            AND project_pub_id=previous.project_pub_id
            AND pub_id=previous.decision_job_pub_id
          FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'metrics_v2_decision_job_not_found' USING ERRCODE='P0002';
          END IF;
          generation := previous_generation + 1;
          decision_hash := encode(public.digest(convert_to(jsonb_build_object(
            'actor_pub_id',NEW.actor_pub_id,
            'generation',generation,
            'method','human',
            'rationale_summary',NEW.rationale_summary,
            'reason_codes',to_jsonb(NEW.reason_codes),
            'result',NEW.result,
            'supersedes_pub_id',NEW.previous_decision_pub_id
          )::text,'UTF8'),'sha256'),'hex');
          decision_idem := encode(public.digest(convert_to(jsonb_build_object(
            'context_hash',previous.context_hash,
            'input_hash',previous.input_hash,
            'judge_policy_hash',previous.judge_policy_hash,
            'rejudge_generation',generation,
            'subject_key',previous.subject_key,
            'task_definition_hash',previous.task_definition_hash,
            'tenant_pub_id',previous.tenant_pub_id
          )::text,'UTF8'),'sha256'),'hex');
          job_pub_id := 'sdj_' || substr(decision_idem,1,26);
          next_decision_pub_id := 'sdr_' || substr(decision_hash,1,26);
          attempt_pub_id := 'sda_' || substr(encode(public.digest(
            convert_to(job_pub_id || chr(58) || 'human','UTF8'),'sha256'),'hex'),1,26);
          recompute_hash := encode(public.digest(convert_to(jsonb_build_object(
            'decision_hash',decision_hash,
            'decision_pub_id',next_decision_pub_id
          )::text,'UTF8'),'sha256'),'hex');
          recompute_pub_id := 'mrj_' || substr(recompute_hash,1,26);

          INSERT INTO analytics.semantic_decision_job_v2
            (pub_id,tenant_pub_id,project_pub_id,task_name,task_version,
             task_definition_hash,subject_type,subject_key,subject_ref,
             input_snapshot_ref,input_hash,context_hash,judge_policy_hash,
             rejudge_generation,supersedes_decision_pub_id,status,idempotency_key,
             retry_count,state_reason_codes,started_at)
          VALUES
            (job_pub_id,previous.tenant_pub_id,previous.project_pub_id,
             previous.task_name,previous.task_version,previous.task_definition_hash,
             previous.subject_type,previous.subject_key,previous.subject_ref,
             previous.input_snapshot_ref,previous.input_hash,previous.context_hash,
             previous.judge_policy_hash,generation,previous.pub_id,'running',
             decision_idem,0,ARRAY['manual_override'],now_at);
          INSERT INTO analytics.semantic_decision_attempt_v2
            (pub_id,tenant_pub_id,project_pub_id,decision_job_pub_id,attempt_index,
             role,method,inference_config,prompt_template_ref,prompt_template_hash,
             rubric_hash,output_schema_hash,request_payload_hash,response_payload_hash,
             validated_output,rationale_summary,validation_status,reason_codes,created_at)
          VALUES
            (attempt_pub_id,previous.tenant_pub_id,previous.project_pub_id,job_pub_id,
             0,'human','human','{{}}'::jsonb,'manual-override',previous.rubric_hash,
             previous.rubric_hash,previous.output_schema_hash,
             NEW.expected_decision_hash,decision_hash,NEW.result,NEW.rationale_summary,
             'valid',NEW.reason_codes,now_at);
          INSERT INTO analytics.semantic_decision_record_v2
            (pub_id,tenant_pub_id,project_pub_id,decision_job_pub_id,task_name,
             task_version,task_definition_hash,subject_type,subject_key,subject_ref,
             metric_name,metric_version,input_snapshot_ref,input_hash,context_hash,
             method,status,result,rationale_summary,calibrated_confidence,
             calibration_bucket,reason_codes,evidence_refs,evidence_spans,
             selected_attempt_pub_ids,judge_policy_hash,rubric_ref,rubric_hash,
             output_schema_hash,supersedes_pub_id,decision_hash,created_at)
          VALUES
            (next_decision_pub_id,previous.tenant_pub_id,previous.project_pub_id,
             job_pub_id,previous.task_name,previous.task_version,
             previous.task_definition_hash,previous.subject_type,previous.subject_key,
             previous.subject_ref,previous.metric_name,previous.metric_version,
             previous.input_snapshot_ref,previous.input_hash,previous.context_hash,
             'human','accepted',NEW.result,NEW.rationale_summary,NULL,NULL,
             NEW.reason_codes,previous.evidence_refs,previous.evidence_spans,
             ARRAY[attempt_pub_id],previous.judge_policy_hash,previous.rubric_ref,
             previous.rubric_hash,previous.output_schema_hash,previous.pub_id,
             decision_hash,now_at);
          UPDATE analytics.semantic_decision_job_v2
          SET status='succeeded',selected_decision_pub_id=next_decision_pub_id,
              completed_at=now_at
          WHERE tenant_pub_id=previous.tenant_pub_id AND pub_id=job_pub_id
            AND status='running';
          INSERT INTO analytics.metric_recompute_job_v2
            (pub_id,tenant_pub_id,project_pub_id,scope,scope_hash,
             target_definition_refs,status,idempotency_key,requested_by)
          VALUES
            (recompute_pub_id,previous.tenant_pub_id,previous.project_pub_id,
             jsonb_build_object('reason','semantic_decision_override',
                                'decision_pub_id',next_decision_pub_id),
             recompute_hash,'[]'::jsonb,'pending',recompute_hash,NEW.actor_pub_id);

          NEW.decision_job_pub_id := job_pub_id;
          NEW.new_decision_pub_id := next_decision_pub_id;
          NEW.new_decision_hash := decision_hash;
          NEW.recompute_job_pub_id := recompute_pub_id;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_semantic_decision_override_command_v2
          INSTEAD OF INSERT ON analytics.semantic_decision_override_command_v2
          FOR EACH ROW EXECUTE FUNCTION analytics.metrics_v2_create_override_command();
        REVOKE ALL ON FUNCTION analytics.metrics_v2_create_override_command() FROM PUBLIC;

        CREATE INDEX ix_qcf_v2_project_query_created
          ON analytics.query_context_fact_v2(tenant_pub_id,project_pub_id,query_key,created_at DESC,pub_id DESC);
        CREATE INDEX ix_qcf_v2_lenses ON analytics.query_context_fact_v2 USING GIN(analysis_lenses);
        CREATE INDEX ix_qcf_v2_operations
          ON analytics.query_context_fact_v2 USING GIN(requested_operations);
        CREATE INDEX ix_qcf_v2_entities
          ON analytics.query_context_fact_v2 USING GIN(detected_entity_ids);
        CREATE INDEX ix_qef_v2_query
          ON analytics.query_entity_exposure_fact_v2(tenant_pub_id,project_pub_id,query_key,created_at DESC);
        CREATE INDEX ix_asm_v2_answer
          ON analytics.answer_semantic_manifest_v2(tenant_pub_id,project_pub_id,answer_pub_id,created_at DESC);
        CREATE INDEX ix_ase_v2_manifest
          ON analytics.answer_semantic_event_v2(tenant_pub_id,semantic_manifest_pub_id,event_index);
        CREATE INDEX ix_ase_v2_answer_type_subject
          ON analytics.answer_semantic_event_v2(tenant_pub_id,answer_pub_id,event_type,subject_entity_id);
        CREATE INDEX ix_seb_v2_subject
          ON analytics.semantic_evidence_bundle_v2(tenant_pub_id,project_pub_id,purpose_task_name,subject_key,created_at DESC);
        CREATE INDEX ix_sdj_v2_queue
          ON analytics.semantic_decision_job_v2(status,created_at,pub_id)
          WHERE status IN ('pending','running');
        CREATE INDEX ix_sdj_v2_subject
          ON analytics.semantic_decision_job_v2(tenant_pub_id,project_pub_id,subject_key,created_at DESC);
        CREATE INDEX ix_sda_v2_job
          ON analytics.semantic_decision_attempt_v2(tenant_pub_id,decision_job_pub_id,attempt_index);
        CREATE INDEX ix_sdr_v2_subject
          ON analytics.semantic_decision_record_v2(tenant_pub_id,project_pub_id,subject_key,created_at DESC);
        CREATE INDEX ix_sdr_v2_task
          ON analytics.semantic_decision_record_v2(tenant_pub_id,task_name,task_version,created_at DESC);
        CREATE INDEX ix_mev_v2_answer_metric
          ON analytics.metric_evaluation_v2(tenant_pub_id,answer_pub_id,metric_name,metric_version);
        CREATE INDEX ix_mss_v2_project_window
          ON analytics.metric_snapshot_set_v2(tenant_pub_id,project_pub_id,window_start,window_end,as_of DESC);
        CREATE INDEX ix_msn_v2_set
          ON analytics.metric_snapshot_v2(tenant_pub_id,snapshot_set_pub_id,metric_name,focal_entity_id);
        CREATE INDEX ix_mct_v2_snapshot
          ON analytics.metric_contribution_v2(tenant_pub_id,snapshot_pub_id,query_key,model,region,mode,capture_time,answer_pub_id);
        CREATE INDEX ix_mct_v2_answer
          ON analytics.metric_contribution_v2(tenant_pub_id,answer_pub_id);
        CREATE INDEX ix_mqc_v2_snapshot_query
          ON analytics.metric_query_contribution_v2(tenant_pub_id,snapshot_pub_id,query_key);
        CREATE INDEX ix_mdc_v2_snapshot
          ON analytics.metric_design_cell_contribution_v2(tenant_pub_id,snapshot_pub_id,query_key,model,region,mode);
        CREATE INDEX ix_mrj_v2_queue
          ON analytics.metric_recompute_job_v2(status,created_at,pub_id)
          WHERE status IN ('pending','running');
        """
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE analytics.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE analytics.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON analytics.{table}
            USING (
              tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')
            )
            WITH CHECK (
              tenant_pub_id = NULLIF(current_setting('app.tenant_pub_id', true), '')
            )
            """
        )
        op.execute(f"REVOKE ALL ON analytics.{table} FROM PUBLIC")

    op.execute(
        """
        CREATE VIEW analytics.query_context_current_v2
        WITH (security_invoker = true) AS
        SELECT ranked.*
        FROM (
          SELECT fact.*,
                 row_number() OVER (
                   PARTITION BY tenant_pub_id,project_pub_id,query_key
                   ORDER BY
                     CASE
                       WHEN review_status IN ('approved','overridden')
                            OR derivation_method='human' THEN 0
                       ELSE 1
                     END,
                     created_at DESC,
                     pub_id DESC
                 ) AS current_rank
          FROM analytics.query_context_fact_v2 fact
          WHERE review_status <> 'rejected'
        ) ranked
        WHERE ranked.current_rank=1;
        REVOKE ALL ON analytics.query_context_current_v2 FROM PUBLIC;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'metrics_v2_append_only:%', TG_TABLE_NAME
            USING ERRCODE='55000';
        END $$;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_guard_definition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_semantic JSONB;
          new_semantic JSONB;
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'definition_delete_forbidden:%', TG_TABLE_NAME
              USING ERRCODE='55000';
          END IF;
          IF OLD.status='published' THEN
            RAISE EXCEPTION 'published_definition_is_immutable:%', TG_TABLE_NAME
              USING ERRCODE='55000';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
            (OLD.status='draft' AND NEW.status='experimental') OR
            (OLD.status='experimental' AND NEW.status='published')
          ) THEN
            RAISE EXCEPTION 'definition_status_transition_invalid:%->%',
              OLD.status,NEW.status USING ERRCODE='55000';
          END IF;
          old_semantic := to_jsonb(OLD) - 'status' - 'published_at';
          new_semantic := to_jsonb(NEW) - 'status' - 'published_at';
          IF TG_TABLE_NAME='metric_definition' THEN
            old_semantic := old_semantic - 'experimental';
            new_semantic := new_semantic - 'experimental';
          END IF;
          IF old_semantic IS DISTINCT FROM new_semantic THEN
            RAISE EXCEPTION 'definition_semantics_are_immutable:%', TG_TABLE_NAME
              USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_guard_decision_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'semantic_decision_job_delete_forbidden' USING ERRCODE='55000';
          END IF;
          IF (OLD.tenant_pub_id,OLD.project_pub_id,OLD.pub_id,OLD.task_name,OLD.task_version,
              OLD.task_definition_hash,OLD.subject_type,OLD.subject_key,OLD.subject_ref,
              OLD.input_snapshot_ref,OLD.input_hash,OLD.context_hash,OLD.judge_policy_hash,
              OLD.rejudge_generation,OLD.supersedes_decision_pub_id,OLD.idempotency_key,
              OLD.created_at)
             IS DISTINCT FROM
             (NEW.tenant_pub_id,NEW.project_pub_id,NEW.pub_id,NEW.task_name,NEW.task_version,
              NEW.task_definition_hash,NEW.subject_type,NEW.subject_key,NEW.subject_ref,
              NEW.input_snapshot_ref,NEW.input_hash,NEW.context_hash,NEW.judge_policy_hash,
              NEW.rejudge_generation,NEW.supersedes_decision_pub_id,NEW.idempotency_key,
              NEW.created_at) THEN
            RAISE EXCEPTION 'semantic_decision_job_identity_is_immutable' USING ERRCODE='55000';
          END IF;
          IF OLD.status IN ('succeeded','abstained','review_required') THEN
            RAISE EXCEPTION 'semantic_decision_job_terminal' USING ERRCODE='55000';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
            (OLD.status='pending' AND NEW.status='running') OR
            (OLD.status='running' AND NEW.status IN
              ('succeeded','abstained','review_required','failed')) OR
            (OLD.status='failed' AND NEW.status='pending')
          ) THEN
            RAISE EXCEPTION 'semantic_decision_job_invalid_transition:%->%',OLD.status,NEW.status
              USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_guard_recompute_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'metric_recompute_job_delete_forbidden' USING ERRCODE='55000';
          END IF;
          IF (OLD.tenant_pub_id,OLD.project_pub_id,OLD.pub_id,OLD.scope,OLD.scope_hash,
              OLD.trigger_event_id,OLD.target_definition_refs,OLD.idempotency_key,
              OLD.requested_by,OLD.created_at)
             IS DISTINCT FROM
             (NEW.tenant_pub_id,NEW.project_pub_id,NEW.pub_id,NEW.scope,NEW.scope_hash,
              NEW.trigger_event_id,NEW.target_definition_refs,NEW.idempotency_key,
              NEW.requested_by,NEW.created_at) THEN
            RAISE EXCEPTION 'metric_recompute_job_identity_is_immutable' USING ERRCODE='55000';
          END IF;
          IF OLD.status='succeeded' THEN
            RAISE EXCEPTION 'metric_recompute_job_terminal' USING ERRCODE='55000';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
            (OLD.status='pending' AND NEW.status='running') OR
            (OLD.status='running' AND NEW.status IN ('succeeded','failed')) OR
            (OLD.status='failed' AND NEW.status='pending')
          ) THEN
            RAISE EXCEPTION 'metric_recompute_job_invalid_transition:%->%',OLD.status,NEW.status
              USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_guard_publication()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'metric_publication_delete_forbidden' USING ERRCODE='55000';
          END IF;
          IF (OLD.tenant_pub_id,OLD.project_pub_id,OLD.pub_id,OLD.scope_hash,
              OLD.publication_channel,OLD.created_at)
             IS DISTINCT FROM
             (NEW.tenant_pub_id,NEW.project_pub_id,NEW.pub_id,NEW.scope_hash,
              NEW.publication_channel,NEW.created_at) THEN
            RAISE EXCEPTION 'metric_publication_identity_is_immutable' USING ERRCODE='55000';
          END IF;
          IF NEW.generation <> OLD.generation + 1 THEN
            RAISE EXCEPTION 'metric_publication_generation_must_increment' USING ERRCODE='40001';
          END IF;
          IF NEW.snapshot_set_pub_id = OLD.snapshot_set_pub_id THEN
            RAISE EXCEPTION 'metric_publication_target_unchanged' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;
        """
    )

    for table in _APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON analytics.{table} FOR EACH ROW "
            "EXECUTE FUNCTION analytics.metrics_v2_reject_mutation()"
        )
    for table in (
        "semantic_decision_task_definition_v2",
        "semantic_judge_policy_v2",
        "metric_definition",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_published_immutable BEFORE UPDATE OR DELETE "
            f"ON analytics.{table} FOR EACH ROW "
            "EXECUTE FUNCTION analytics.metrics_v2_guard_definition()"
        )
    op.execute(
        "CREATE TRIGGER trg_semantic_decision_job_v2_state BEFORE UPDATE OR DELETE "
        "ON analytics.semantic_decision_job_v2 FOR EACH ROW "
        "EXECUTE FUNCTION analytics.metrics_v2_guard_decision_job()"
    )
    op.execute(
        "CREATE TRIGGER trg_metric_recompute_job_v2_state BEFORE UPDATE OR DELETE "
        "ON analytics.metric_recompute_job_v2 FOR EACH ROW "
        "EXECUTE FUNCTION analytics.metrics_v2_guard_recompute_job()"
    )
    op.execute(
        "CREATE TRIGGER trg_metric_publication_v2_cas BEFORE UPDATE OR DELETE "
        "ON analytics.metric_publication_v2 FOR EACH ROW "
        "EXECUTE FUNCTION analytics.metrics_v2_guard_publication()"
    )

    op.execute(migration_reconcile_sql(API_ROLE))
    op.execute(migration_reconcile_sql(WORKER_ROLE))


def downgrade() -> None:
    populated = " OR ".join(
        f"EXISTS (SELECT 1 FROM analytics.{table} LIMIT 1)" for table in _TENANT_TABLES
    )
    op.execute(
        f"""
        DO $$ BEGIN
          IF {populated} THEN
            RAISE EXCEPTION 'geo_metrics_v2_history_present_downgrade_refused';
          END IF;
          IF EXISTS (
            SELECT 1 FROM analytics.metric_definition WHERE status <> 'legacy'
          ) THEN
            RAISE EXCEPTION 'geo_metrics_v2_definitions_present_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.execute("DROP VIEW IF EXISTS analytics.query_context_current_v2")
    op.execute(
        """
        ALTER TABLE reporting.formal_report_production
          DROP CONSTRAINT IF EXISTS formal_metric_snapshot_set_fk,
          DROP CONSTRAINT IF EXISTS formal_metric_snapshot_binding_ck,
          DROP COLUMN IF EXISTS metric_snapshot_dependency_hash,
          DROP COLUMN IF EXISTS metric_snapshot_filters,
          DROP COLUMN IF EXISTS metric_snapshot_set_hash,
          DROP COLUMN IF EXISTS metric_snapshot_set_pub_id
        """
    )
    op.execute(
        """
        DROP VIEW IF EXISTS analytics.semantic_decision_override_command_v2 CASCADE;
        DROP FUNCTION IF EXISTS analytics.metrics_v2_create_override_command()
        """
    )
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS analytics.{table} CASCADE")
    for table in (
        "semantic_judge_policy_v2",
        "semantic_decision_task_definition_v2",
    ):
        op.execute(f"DROP TABLE analytics.{table}")
    op.execute("DROP INDEX IF EXISTS analytics.uq_metric_definition_v2_hash")
    op.execute(
        """
        DROP TRIGGER trg_metric_definition_published_immutable
          ON analytics.metric_definition;
        ALTER TABLE analytics.metric_definition
          DROP CONSTRAINT IF EXISTS ck_metric_definition_v2_published,
          DROP CONSTRAINT IF EXISTS ck_metric_definition_v2_hash,
          DROP COLUMN published_at,
          DROP COLUMN publication_gate,
          DROP COLUMN default_aggregation_method,
          DROP COLUMN allowed_aggregation_methods,
          DROP COLUMN adjudication_uncertainty_policy,
          DROP COLUMN semantic_rubric_ref,
          DROP COLUMN outcome_source,
          DROP COLUMN decision_task_refs,
          DROP COLUMN required_semantic_capabilities,
          DROP COLUMN required_event_types,
          DROP COLUMN unit_type,
          DROP COLUMN status,
          DROP COLUMN definition_hash,
          DROP COLUMN definition_schema_version;
        ALTER TABLE reporting.data_export DROP CONSTRAINT ck_data_export_type;
        ALTER TABLE reporting.data_export ADD CONSTRAINT data_export_export_type_check
          CHECK (export_type IN ('metric_xlsx'));
        DROP FUNCTION analytics.metrics_v2_guard_publication();
        DROP FUNCTION analytics.metrics_v2_guard_recompute_job();
        DROP FUNCTION analytics.metrics_v2_guard_decision_job();
        DROP FUNCTION analytics.metrics_v2_guard_definition();
        DROP FUNCTION analytics.metrics_v2_reject_mutation();
        """
    )


__all__ = ["downgrade", "upgrade"]
