# ruff: noqa: E501
"""Extend Metrics V2 through a forward-only schema migration.

Revision ID: s18_0003_metrics_v2_failure
Revises: s18_0002_knowledge_model_lineage
"""

from collections.abc import Sequence

from alembic import op
from geo_platform.tenancy.runtime_acl import API_ROLE, WORKER_ROLE, migration_reconcile_sql

revision: str = "s18_0003_metrics_v2_failure"
down_revision: str | Sequence[str] | None = "s18_0002_knowledge_model_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HASH = r"^[0-9a-f]{64}$"

_UPGRADED_OVERRIDE_SQL = f"""
        DROP VIEW analytics.semantic_decision_override_command_v2 CASCADE;
        CREATE VIEW analytics.semantic_decision_override_command_v2 AS
        SELECT
          NULL::TEXT AS tenant_pub_id,
          NULL::TEXT AS project_pub_id,
          NULL::TEXT AS previous_decision_pub_id,
          NULL::JSONB AS result,
          NULL::TEXT AS rationale_summary,
          NULL::TEXT[] AS reason_codes,
          NULL::TEXT AS expected_decision_hash,
          NULL::TEXT AS actor_pub_id,
          NULL::TEXT AS decision_job_pub_id,
          NULL::TEXT AS human_attempt_pub_id,
          NULL::TEXT AS new_decision_pub_id,
          NULL::TEXT AS new_decision_hash,
          NULL::TEXT AS recompute_job_pub_id
        WHERE false;

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_create_override_command()
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
             OR nullif(btrim(NEW.project_pub_id),'') IS NULL
             OR nullif(btrim(NEW.rationale_summary),'') IS NULL
             OR cardinality(NEW.reason_codes)=0
             OR NEW.decision_job_pub_id !~ '^sdj_.+'
             OR NEW.human_attempt_pub_id !~ '^sda_.+'
             OR NEW.new_decision_pub_id !~ '^sdr_.+'
             OR NEW.new_decision_hash !~ '{_HASH}' THEN
            RAISE EXCEPTION 'metrics_v2_override_input_invalid' USING ERRCODE='22023';
          END IF;
          SELECT * INTO previous
          FROM analytics.semantic_decision_record_v2
          WHERE tenant_pub_id=NEW.tenant_pub_id
            AND project_pub_id=NEW.project_pub_id
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
            AND project_pub_id=NEW.project_pub_id
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
          decision_hash := NEW.new_decision_hash;
          decision_idem := encode(public.digest(convert_to(jsonb_build_object(
            'context_hash',previous.context_hash,
            'input_hash',previous.input_hash,
            'judge_policy_hash',previous.judge_policy_hash,
            'rejudge_generation',generation,
            'subject_key',previous.subject_key,
            'task_definition_hash',previous.task_definition_hash,
            'tenant_pub_id',previous.tenant_pub_id
          )::text,'UTF8'),'sha256'),'hex');
          job_pub_id := NEW.decision_job_pub_id;
          next_decision_pub_id := NEW.new_decision_pub_id;
          attempt_pub_id := NEW.human_attempt_pub_id;
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
"""

_BASELINE_OVERRIDE_SQL = """
        DROP VIEW analytics.semantic_decision_override_command_v2 CASCADE;
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

        CREATE OR REPLACE FUNCTION analytics.metrics_v2_create_override_command()
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
             0,'human','human','{}'::jsonb,'manual-override',previous.rubric_hash,
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
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE analytics.semantic_judge_policy_v2
          DROP CONSTRAINT ck_semantic_judge_policy_published,
          ADD CONSTRAINT ck_semantic_judge_policy_published CHECK (
            status <> 'published' OR published_at IS NOT NULL
          );

        ALTER TABLE analytics.answer_semantic_manifest_v2
          DROP CONSTRAINT uq_asm_v2_identity,
          ADD CONSTRAINT uq_asm_v2_identity UNIQUE (
            tenant_pub_id,answer_pub_id,query_context_fact_pub_id,
            input_hash,extractor_bundle_hash,decision_task_bundle_hash,
            entity_dictionary_hash,decision_set_hash
          );

        ALTER TABLE analytics.metric_evaluation_v2
          DROP CONSTRAINT metric_evaluation_v2_eligibility_status_check,
          ADD CONSTRAINT metric_evaluation_v2_eligibility_status_check CHECK (
            eligibility_status IN (
              'included_hit','included_miss','excluded','not_applicable',
              'analysis_unknown','analysis_failed'
            )
          );

        ALTER TABLE analytics.metric_snapshot_v2
          ADD COLUMN failed_answer_count BIGINT NOT NULL DEFAULT 0
            CHECK (failed_answer_count >= 0),
          DROP CONSTRAINT ck_msn_v2_counts,
          ADD CONSTRAINT ck_msn_v2_counts CHECK (
            known_answer_count + unknown_answer_count + failed_answer_count
              <= candidate_answer_count
            AND not_applicable_answer_count + excluded_answer_count
              <= candidate_answer_count
          );

        ALTER TABLE analytics.metric_contribution_v2
          DROP CONSTRAINT metric_contribution_v2_eligibility_status_check,
          ADD CONSTRAINT metric_contribution_v2_eligibility_status_check CHECK (
            eligibility_status IN (
              'included_hit','included_miss','excluded','not_applicable',
              'analysis_unknown','analysis_failed'
            )
          );
        """
    )
    op.execute(_UPGRADED_OVERRIDE_SQL)
    op.execute(migration_reconcile_sql(API_ROLE))
    op.execute(migration_reconcile_sql(WORKER_ROLE))


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM analytics.semantic_judge_policy_v2
            WHERE status='published' AND calibration_artifact_hash IS NULL
          ) THEN
            RAISE EXCEPTION 'metrics_v2_uncalibrated_policy_downgrade_refused';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM analytics.answer_semantic_manifest_v2
            GROUP BY tenant_pub_id,answer_pub_id,input_hash,extractor_bundle_hash,
                     decision_task_bundle_hash,entity_dictionary_hash
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'metrics_v2_manifest_identity_downgrade_refused';
          END IF;
          IF EXISTS (
            SELECT 1 FROM analytics.metric_evaluation_v2
            WHERE eligibility_status='analysis_failed'
          ) OR EXISTS (
            SELECT 1 FROM analytics.metric_contribution_v2
            WHERE eligibility_status='analysis_failed'
          ) OR EXISTS (
            SELECT 1 FROM analytics.metric_snapshot_v2
            WHERE failed_answer_count <> 0
          ) THEN
            RAISE EXCEPTION 'metrics_v2_failure_projection_downgrade_refused';
          END IF;
        END $$;
        """
    )
    op.execute(_BASELINE_OVERRIDE_SQL)
    op.execute(
        """
        ALTER TABLE analytics.metric_contribution_v2
          DROP CONSTRAINT metric_contribution_v2_eligibility_status_check,
          ADD CONSTRAINT metric_contribution_v2_eligibility_status_check CHECK (
            eligibility_status IN (
              'included_hit','included_miss','excluded','not_applicable','analysis_unknown'
            )
          );

        ALTER TABLE analytics.metric_snapshot_v2
          DROP CONSTRAINT ck_msn_v2_counts,
          ADD CONSTRAINT ck_msn_v2_counts CHECK (
            known_answer_count + unknown_answer_count <= candidate_answer_count
            AND not_applicable_answer_count + excluded_answer_count
              <= candidate_answer_count
          ),
          DROP COLUMN failed_answer_count;

        ALTER TABLE analytics.metric_evaluation_v2
          DROP CONSTRAINT metric_evaluation_v2_eligibility_status_check,
          ADD CONSTRAINT metric_evaluation_v2_eligibility_status_check CHECK (
            eligibility_status IN (
              'included_hit','included_miss','excluded','not_applicable','analysis_unknown'
            )
          );

        ALTER TABLE analytics.answer_semantic_manifest_v2
          DROP CONSTRAINT uq_asm_v2_identity,
          ADD CONSTRAINT uq_asm_v2_identity UNIQUE (
            tenant_pub_id,answer_pub_id,input_hash,extractor_bundle_hash,
            decision_task_bundle_hash,entity_dictionary_hash
          );

        ALTER TABLE analytics.semantic_judge_policy_v2
          DROP CONSTRAINT ck_semantic_judge_policy_published,
          ADD CONSTRAINT ck_semantic_judge_policy_published CHECK (
            status <> 'published' OR (
              published_at IS NOT NULL AND calibration_artifact_hash IS NOT NULL
            )
          );
        """
    )
    op.execute(migration_reconcile_sql(API_ROLE))
    op.execute(migration_reconcile_sql(WORKER_ROLE))


__all__ = ["downgrade", "upgrade"]
