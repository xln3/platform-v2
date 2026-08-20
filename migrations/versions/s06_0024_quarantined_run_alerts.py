"""Exclude explicitly quarantined collection runs from stalled-run alerting.

Revision ID: s06_0024
Revises: s06_0023

An operator may need to preserve an orphaned run for forensic continuity without
resuming or cancelling it.  Such a run remains in its truthful workflow state,
but ``error_code='operator_quarantined'`` makes the operational intent explicit.
Only that marked exception is excluded; every other stale active run continues
to contribute to ``GeoCollectionRunStalled``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0024"
down_revision: str | Sequence[str] | None = "s06_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION_SQL = """
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
    {quarantine_filter}
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


def _install(*, exclude_quarantined: bool) -> None:
    quarantine_filter = (
        "AND COALESCE(error_code,'') <> 'operator_quarantined'" if exclude_quarantined else ""
    )
    op.execute(_FUNCTION_SQL.format(quarantine_filter=quarantine_filter))


def upgrade() -> None:
    _install(exclude_quarantined=True)


def downgrade() -> None:
    _install(exclude_quarantined=False)
