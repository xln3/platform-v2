"""人工补测登记通道：channel 词表扩 'manual'（manual-ingestion-v1）。

Revision ID: s20_0001_manual_channel
Revises: s19_0001_account_lease

背景：客户报告会话中平台风控导致个别题爬不动、由运营在浏览器人工实测的
回答，需要登记为带 provenance 的正式 ``analytics.answer`` 行（channel=
manual，登记链=``api/geo_platform/analytics/manual_ingestion.py``）。
``domain.evidence.provenance.CaptureChannel`` 新增 ``MANUAL="manual"``，
本迁移把两处引用该词表的 CHECK 约束同步放宽（s02_0001 建表时内联声明、
Postgres 自动命名）：

1. ``analytics.answer_analysis.channel``：分析投影写 answer_analysis 时透传
   provenance.channel——不放宽则 manual 行在分析落库即违反 CHECK。
2. ``evidence.evidence_asset.channel``：人工补测的截图等证据资产后续以
   channel='manual' 入 CAS 时需要（本期登记只关联既有资产，不新建）。

``analytics.answer.channel`` 自 s04_0004 起为无 CHECK 的 TEXT，无需变更。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s20_0001_manual_channel"
down_revision: str | Sequence[str] | None = "s19_0001_account_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 幂等守卫：试点登记需要在 release 跑迁移前手工应用同一 SQL——约束定义
    # 已含 'manual' 时整块跳过，alembic 正式升级重放安全。
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='analytics.answer_analysis'::regclass
              AND conname='answer_analysis_channel_check'
              AND pg_get_constraintdef(oid) LIKE '%''manual''%'
          ) THEN
            ALTER TABLE analytics.answer_analysis
              DROP CONSTRAINT IF EXISTS answer_analysis_channel_check;
            ALTER TABLE analytics.answer_analysis
              ADD CONSTRAINT answer_analysis_channel_check
                CHECK (channel IN ('api','web','manual'));
          END IF;
        END $$;
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='evidence.evidence_asset'::regclass
              AND conname='evidence_asset_channel_check'
              AND pg_get_constraintdef(oid) LIKE '%''manual''%'
          ) THEN
            ALTER TABLE evidence.evidence_asset
              DROP CONSTRAINT IF EXISTS evidence_asset_channel_check;
            ALTER TABLE evidence.evidence_asset
              ADD CONSTRAINT evidence_asset_channel_check
                CHECK (channel IN ('api','web','manual'));
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE analytics.answer_analysis
          DROP CONSTRAINT answer_analysis_channel_check,
          ADD CONSTRAINT answer_analysis_channel_check
            CHECK (channel IN ('api','web'));
        ALTER TABLE evidence.evidence_asset
          DROP CONSTRAINT evidence_asset_channel_check,
          ADD CONSTRAINT evidence_asset_channel_check
            CHECK (channel IN ('api','web'));
        """
    )
