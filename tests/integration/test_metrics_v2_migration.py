from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

pytestmark = pytest.mark.isolated_postgres

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)


def test_metrics_v2_physical_contract_is_installed_at_head() -> None:
    expected = {
        "query_context_fact_v2",
        "query_entity_exposure_fact_v2",
        "answer_semantic_manifest_v2",
        "answer_semantic_event_v2",
        "semantic_decision_task_definition_v2",
        "semantic_judge_policy_v2",
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
    }
    with psycopg.connect(POSTGRES_DSN) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "s18_0003_metrics_v2_failure",
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='analytics' AND table_name=ANY(%s::text[])
                """,
                (sorted(expected),),
            ).fetchall()
        }
        assert tables == expected
        metric_columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='analytics' AND table_name='metric_definition'
                """
            ).fetchall()
        }
        assert {
            "definition_schema_version",
            "definition_hash",
            "status",
            "unit_type",
            "decision_task_refs",
            "publication_gate",
        } <= metric_columns
        export_constraint = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid='reporting.data_export'::regclass
              AND conname='ck_data_export_type'
            """
        ).fetchone()
        assert export_constraint is not None
        assert "metric_v2_xlsx" in str(export_constraint[0])
        assert "metric_v2_csv_zip" in str(export_constraint[0])
        formal_columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='reporting'
                  AND table_name='formal_report_production'
                  AND column_name LIKE 'metric_snapshot_%'
                """
            ).fetchall()
        }
        assert formal_columns == {
            "metric_snapshot_set_pub_id",
            "metric_snapshot_set_hash",
            "metric_snapshot_filters",
            "metric_snapshot_dependency_hash",
        }
        formal_fk = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid='reporting.formal_report_production'::regclass
              AND conname='formal_metric_snapshot_set_fk'
            """
        ).fetchone()
        assert formal_fk is not None
        assert "tenant_pub_id, project_pub_id, metric_snapshot_set_pub_id" in str(formal_fk[0])
        scalar_policy_columns = dict(
            connection.execute(
                """
                SELECT table_name || '.' || column_name,data_type
                FROM information_schema.columns
                WHERE table_schema='analytics' AND (
                  (table_name='semantic_decision_task_definition_v2'
                   AND column_name='decision_method_policy') OR
                  (table_name='semantic_judge_policy_v2'
                   AND column_name='disagreement_policy')
                )
                """
            ).fetchall()
        )
        assert scalar_policy_columns == {
            "semantic_decision_task_definition_v2.decision_method_policy": "text",
            "semantic_judge_policy_v2.disagreement_policy": "text",
        }
        assert connection.execute(
            """
            SELECT column_default,is_nullable
            FROM information_schema.columns
            WHERE table_schema='analytics' AND table_name='metric_snapshot_v2'
              AND column_name='failed_answer_count'
            """
        ).fetchone() == ("0", "NO")
        override_columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='analytics'
                  AND table_name='semantic_decision_override_command_v2'
                """
            ).fetchall()
        }
        assert "project_pub_id" in override_columns
        assert connection.execute(
            """
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid='analytics.semantic_decision_override_command_v2'::regclass
              AND tgname='trg_semantic_decision_override_command_v2'
              AND NOT tgisinternal
            """
        ).fetchone() == (1,)
        override_function = connection.execute(
            """
            SELECT pg_get_functiondef(
              'analytics.metrics_v2_create_override_command()'::regprocedure
            )
            """
        ).fetchone()
        assert override_function is not None
        assert "project_pub_id=NEW.project_pub_id" in str(override_function[0])
        assert "metrics_v2_canonical_json" in str(override_function[0])


def test_metrics_v2_ratios_use_fixed_precision_and_history_has_guards() -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        numeric = connection.execute(
            """
            SELECT numeric_precision,numeric_scale
            FROM information_schema.columns
            WHERE table_schema='analytics' AND table_name='metric_snapshot_v2'
              AND column_name='value'
            """
        ).fetchone()
        assert numeric == (20, 12)
        triggers = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid IN (
                  'analytics.query_context_fact_v2'::regclass,
                  'analytics.semantic_decision_record_v2'::regclass,
                  'analytics.metric_snapshot_set_v2'::regclass,
                  'analytics.metric_contribution_v2'::regclass
                ) AND NOT tgisinternal
                """
            ).fetchall()
        }
        assert {
            "trg_query_context_fact_v2_append_only",
            "trg_semantic_decision_record_v2_append_only",
            "trg_metric_snapshot_set_v2_append_only",
            "trg_metric_contribution_v2_append_only",
        } <= triggers


def test_judge_policy_publication_requires_timestamp_but_not_calibration() -> None:
    token = uuid4().hex
    insert = """
        INSERT INTO analytics.semantic_judge_policy_v2 (
          name,version,compatible_task_refs,method_pipeline,model_routes,
          inference_configs,timeout_retry_policy,acceptance_thresholds,
          disagreement_policy,evidence_budget,cost_budget,fallback_policy,
          calibration_artifact_hash,policy_hash,status,published_at
        ) VALUES (
          %s,'1.0.0','[]'::jsonb,'{}'::jsonb,'{}'::jsonb,
          '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'review',
          '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,%s,%s,%s,%s
        )
    """
    with psycopg.connect(POSTGRES_DSN, autocommit=True) as connection:
        connection.execute(
            insert,
            (f"experimental-{token}", None, token * 2, "experimental", None),
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                UPDATE analytics.semantic_judge_policy_v2
                SET disagreement_policy='adjudicate' WHERE name=%s
                """,
                (f"experimental-{token}",),
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM analytics.semantic_judge_policy_v2 WHERE name=%s",
                (f"experimental-{token}",),
            )
        published_name = f"published-{token}"
        connection.execute(
            insert,
            (
                published_name,
                None,
                uuid4().hex * 2,
                "published",
                "2026-01-01T00:00:00Z",
            ),
        )
        assert connection.execute(
            """
            SELECT status,calibration_artifact_hash
            FROM analytics.semantic_judge_policy_v2 WHERE name=%s
            """,
            (published_name,),
        ).fetchone() == ("published", None)

        lifecycle_name = f"lifecycle-{token}"
        calibration_hash = uuid4().hex * 2
        connection.execute(
            insert,
            (lifecycle_name, calibration_hash, uuid4().hex * 2, "draft", None),
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                UPDATE analytics.semantic_judge_policy_v2
                SET status='published',published_at='2026-01-01T00:00:00Z'
                WHERE name=%s
                """,
                (lifecycle_name,),
            )
        connection.execute(
            """
            UPDATE analytics.semantic_judge_policy_v2
            SET status='experimental' WHERE name=%s
            """,
            (lifecycle_name,),
        )
        connection.execute(
            """
            UPDATE analytics.semantic_judge_policy_v2
            SET status='published',published_at='2026-01-01T00:00:00Z'
            WHERE name=%s
            """,
            (lifecycle_name,),
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                UPDATE analytics.semantic_judge_policy_v2
                SET published_at='2026-01-02T00:00:00Z' WHERE name=%s
                """,
                (lifecycle_name,),
            )
