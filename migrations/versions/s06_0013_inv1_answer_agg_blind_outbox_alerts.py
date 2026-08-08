"""INV-1 blind aggregate view + analytics outbox backlog/quarantine alerting.

Revision ID: s06_0013
Revises: s06_0012

三件事：

1. ``analytics.answer_agg_blind`` 视图（INV-1 测量盲读投影，对齐旧系统
   ``server/geosys/schema.sql`` 的 ``answer_agg_blind`` 语义：合格且未降级的
   答案全集）。``security_invoker=on``——以调用者身份跑基表 RLS（视图 owner
   是迁移角色，缺省 security_definer 语义在 owner 为超级用户时会绕过
   FORCE RLS，必须显式 invoker）。
   历史存量行口径：2026-08-08 前的写入路径无五元 provenance，eligible 恒
   true（继承现状的结构保证，不回填不改写）；此后由
   ``domain/scoring/eligibility.resolve_measurement_eligibility`` 真实计算。
2. ``integration.outbox_event`` 告警索引：按 (event_type, occurred_at) 扫
   未发布行（毒消息兜底后 attempts<8 的健康积压与 attempts>=8 的隔离队列）。
3. ``integration.business_alert_snapshot()`` 追加两个指标：
   - ``analytics_outbox_backlog``（dimension=event_type）：分析投影类事件
     未发布且未隔离、超过 15 分钟（collection.run.completed 的积压已由
     analysis_admission_backlog 覆盖，不重复计）；
   - ``analytics_outbox_quarantined``：attempts>=8 被隔离等人工的事件。
   事件词表单源=api/geo_platform/analytics/outbox.py ANALYTICS_EVENT_TYPES；
   隔离阈值单源=同文件 OUTBOX_MAX_ATTEMPTS（8）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0013"
down_revision: str | Sequence[str] | None = "s06_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW analytics.answer_agg_blind
        WITH (security_invoker=on) AS
        SELECT id,pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,
               response_text,model,region,mode,eligible,degraded,channel,
               adapter_version,capture_time,created_at,run_pub_id,
               config_version_pub_id
        FROM analytics.answer
        WHERE eligible AND NOT degraded;
        COMMENT ON VIEW analytics.answer_agg_blind IS
          'INV-1 测量盲读视图：仅 eligible 且非 degraded 答案（对齐旧系统语义）。'
          '2026-08-08 前存量行 eligible=true 是继承现状的结构保证（旧写入路径无'
          '五元 provenance）；此后由 measurement_eligible 真实计算。';
        CREATE INDEX ix_analytics_outbox_business_alert
          ON integration.outbox_event (event_type,occurred_at)
          WHERE published_at IS NULL;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo') THEN
            GRANT SELECT ON analytics.answer_agg_blind TO geo;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT SELECT ON analytics.answer_agg_blind TO geo_worker;
          END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION integration.business_alert_snapshot()
        RETURNS TABLE(metric text,dimension text,value bigint)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog
        AS $function$
          SELECT 'tenant_count','',count(*)
          FROM platform.tenant
          WHERE state='active'
          UNION ALL
          SELECT 'workflow_start_stale','',count(*)
          FROM integration.workflow_start_command
          WHERE state IN ('pending','dispatching')
            AND updated_at < now()-interval '5 minutes'
          UNION ALL
          SELECT 'workflow_signal_stale','',count(*)
          FROM integration.workflow_signal_command
          WHERE state IN ('pending','dispatching')
            AND updated_at < now()-interval '5 minutes'
          UNION ALL
          SELECT 'collection_run_stalled','',count(*)
          FROM platform.collection_run
          WHERE state IN (
            'pending','starting','running','pausing','paused','resuming','cancelling'
          )
            AND updated_at < now()-interval '1 hour'
          UNION ALL
          SELECT 'revocation_stalled','',count(*)
          FROM platform.revocation_request
          WHERE state IN ('requested','starting','running')
            AND updated_at < now()-interval '15 minutes'
          UNION ALL
          SELECT 'expired_session_leases','',count(*)
          FROM platform.session_lease
          WHERE released_at IS NULL AND expires_at < now()
          UNION ALL
          SELECT 'report_delivery_overdue','',count(*)
          FROM reporting.report_delivery
          WHERE confirmed_at IS NULL
            AND delivered_at < now()-interval '7 days'
          UNION ALL
          SELECT
            'analysis_admission_backlog',
            reasons.reason,
            count(events.event_id)
          FROM (
            VALUES
              ('not_requested'),
              ('missing_brand'),
              ('missing_completed_answers'),
              ('partial_fanout'),
              ('unknown')
          ) AS reasons(reason)
          LEFT JOIN (
            SELECT
              event_id,
              CASE
                WHEN COALESCE(payload->>'analysis_admission','not_requested') IN (
                  'not_requested','missing_brand','missing_completed_answers','partial_fanout'
                )
                THEN COALESCE(payload->>'analysis_admission','not_requested')
                ELSE 'unknown'
              END AS reason
            FROM integration.outbox_event
            WHERE event_type='collection.run.completed'
              AND published_at IS NULL
              AND occurred_at < now()-interval '15 minutes'
          ) events ON events.reason=reasons.reason
          GROUP BY reasons.reason
          UNION ALL
          SELECT 'analytics_outbox_backlog',event_type,count(*)
          FROM integration.outbox_event
          WHERE published_at IS NULL
            AND attempts < 8
            AND occurred_at < now()-interval '15 minutes'
            AND event_type IN (
              'analytics.answer.analyzed','disparagement.recorded',
              'intelligence.feature.recorded','source_audit.recorded'
            )
          GROUP BY event_type
          UNION ALL
          SELECT 'analytics_outbox_quarantined',event_type,count(*)
          FROM integration.outbox_event
          WHERE published_at IS NULL
            AND attempts >= 8
            AND event_type IN (
              'analytics.answer.analyzed','collection.run.completed',
              'disparagement.recorded','intelligence.feature.recorded',
              'source_audit.recorded'
            )
          GROUP BY event_type
        $function$;

        REVOKE ALL ON FUNCTION integration.business_alert_snapshot() FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT EXECUTE ON FUNCTION integration.business_alert_snapshot() TO geo_worker;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS analytics.answer_agg_blind;
        DROP INDEX IF EXISTS integration.ix_analytics_outbox_business_alert;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION integration.business_alert_snapshot()
        RETURNS TABLE(metric text,dimension text,value bigint)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path=pg_catalog
        AS $function$
          SELECT 'tenant_count','',count(*)
          FROM platform.tenant
          WHERE state='active'
          UNION ALL
          SELECT 'workflow_start_stale','',count(*)
          FROM integration.workflow_start_command
          WHERE state IN ('pending','dispatching')
            AND updated_at < now()-interval '5 minutes'
          UNION ALL
          SELECT 'workflow_signal_stale','',count(*)
          FROM integration.workflow_signal_command
          WHERE state IN ('pending','dispatching')
            AND updated_at < now()-interval '5 minutes'
          UNION ALL
          SELECT 'collection_run_stalled','',count(*)
          FROM platform.collection_run
          WHERE state IN (
            'pending','starting','running','pausing','paused','resuming','cancelling'
          )
            AND updated_at < now()-interval '1 hour'
          UNION ALL
          SELECT 'revocation_stalled','',count(*)
          FROM platform.revocation_request
          WHERE state IN ('requested','starting','running')
            AND updated_at < now()-interval '15 minutes'
          UNION ALL
          SELECT 'expired_session_leases','',count(*)
          FROM platform.session_lease
          WHERE released_at IS NULL AND expires_at < now()
          UNION ALL
          SELECT 'report_delivery_overdue','',count(*)
          FROM reporting.report_delivery
          WHERE confirmed_at IS NULL
            AND delivered_at < now()-interval '7 days'
          UNION ALL
          SELECT
            'analysis_admission_backlog',
            reasons.reason,
            count(events.event_id)
          FROM (
            VALUES
              ('not_requested'),
              ('missing_brand'),
              ('missing_completed_answers'),
              ('partial_fanout'),
              ('unknown')
          ) AS reasons(reason)
          LEFT JOIN (
            SELECT
              event_id,
              CASE
                WHEN COALESCE(payload->>'analysis_admission','not_requested') IN (
                  'not_requested','missing_brand','missing_completed_answers','partial_fanout'
                )
                THEN COALESCE(payload->>'analysis_admission','not_requested')
                ELSE 'unknown'
              END AS reason
            FROM integration.outbox_event
            WHERE event_type='collection.run.completed'
              AND published_at IS NULL
              AND occurred_at < now()-interval '15 minutes'
          ) events ON events.reason=reasons.reason
          GROUP BY reasons.reason
        $function$;

        REVOKE ALL ON FUNCTION integration.business_alert_snapshot() FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='geo_worker') THEN
            GRANT EXECUTE ON FUNCTION integration.business_alert_snapshot() TO geo_worker;
          END IF;
        END
        $$;
        """
    )
