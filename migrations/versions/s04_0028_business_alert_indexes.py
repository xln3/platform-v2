"""Add bounded business-alert query indexes.

Revision ID: s04_0028
Revises: s04_0027
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s04_0028"
down_revision: str | Sequence[str] | None = "s04_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX ix_collection_run_business_alert
          ON platform.collection_run (tenant_id,updated_at)
          WHERE state IN (
            'pending','starting','running','pausing','paused','resuming','cancelling'
          );
        CREATE INDEX ix_session_lease_business_alert
          ON platform.session_lease (tenant_id,expires_at)
          WHERE released_at IS NULL;
        CREATE INDEX ix_revocation_request_business_alert
          ON platform.revocation_request (tenant_id,updated_at)
          WHERE state IN ('requested','starting','running');
        CREATE INDEX ix_report_delivery_business_alert
          ON reporting.report_delivery (tenant_pub_id,delivered_at)
          WHERE confirmed_at IS NULL;
        CREATE INDEX ix_completion_outbox_business_alert
          ON integration.outbox_event (tenant_pub_id,occurred_at)
          WHERE event_type='collection.run.completed' AND published_at IS NULL;
        """
    )
    op.execute(
        """
        CREATE FUNCTION integration.business_alert_snapshot()
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


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS integration.business_alert_snapshot();
        DROP INDEX IF EXISTS integration.ix_completion_outbox_business_alert;
        DROP INDEX IF EXISTS reporting.ix_report_delivery_business_alert;
        DROP INDEX IF EXISTS platform.ix_revocation_request_business_alert;
        DROP INDEX IF EXISTS platform.ix_session_lease_business_alert;
        DROP INDEX IF EXISTS platform.ix_collection_run_business_alert;
        """
    )
