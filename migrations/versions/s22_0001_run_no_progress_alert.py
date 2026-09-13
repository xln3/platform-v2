"""run 停滞可配阈值计数函数（collection-run-no-progress-v1）。

Revision ID: s22_0001_run_no_progress
Revises: s21_0001_merge_heads

背景（2026-09-10 汇宜 run 卡死劫持复盘）：既有
``integration.business_alert_snapshot()`` 的 ``collection_run_stalled``
阈值硬编码 1 小时——单题卡死拖住整 run ~32 分钟的真实事故够不到阈值，
全程零告警。本迁移新增带阈值形参的 SECURITY DEFINER 计数函数，阈值由
business_metrics  exporter 的 env（``GEO_BUSINESS_RUN_STALL_WARN_MINUTES``，
缺省 20 分钟）注入——调阈值不再要写迁移。

口径：等待启动/运行/恢复中的 run，排除人为暂停和 ``operator_quarantined``；
最近终态任务的更新时间作为进展，无终态任务时从 run 创建时间起算。函数缺失
期间 exporter 跳过该指标（to_regprocedure 守卫），迁移/发布乱序安全。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s22_0001_run_no_progress"
down_revision: str | Sequence[str] | None = "s21_0001_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION integration.collection_run_no_progress_count(
          stale_seconds integer
        )
        RETURNS bigint
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path TO 'pg_catalog'
        AS $function$
          SELECT count(*)
          FROM platform.collection_run AS run
          WHERE state IN (
            'pending','starting','running','resuming'
          )
            AND greatest(run.created_at, COALESCE((
              SELECT max(task.updated_at)
              FROM platform.collection_task AS task
              WHERE task.run_id = run.id AND task.state IN ('completed', 'failed')
            ), run.created_at)) < now() - make_interval(secs => stale_seconds)
            AND COALESCE(error_code,'') <> 'operator_quarantined'
        $function$;
        REVOKE ALL ON FUNCTION integration.collection_run_no_progress_count(integer) FROM PUBLIC;
        DO $grant$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'geo_worker') THEN
            GRANT EXECUTE ON FUNCTION integration.collection_run_no_progress_count(integer)
              TO geo_worker;
          END IF;
        END
        $grant$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS integration.collection_run_no_progress_count(integer);")
