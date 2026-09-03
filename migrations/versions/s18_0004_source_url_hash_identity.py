"""source_url 身份唯一约束改为 (tenant_id, canonical_url_hash)（长 URL 修复）。

Revision ID: s18_0004_url_hash_identity
Revises: s18_0003_metrics_v2_failure

背景（2026-09-02 费列罗基线事故）：文心答案引用真实出现 2718 字符的
``www.baidu.com/baidu.php?url=...`` 追踪重定向 URL。btree v4 索引行上限
2704 字节，``uq_source_url_identity`` 把 ``canonical_url`` 全文编进索引导致
``ProgramLimitExceeded``，整条采集 workflow 被炸死。

sha256（``canonical_url_hash``，有 ``^[0-9a-f]{64}$`` CHECK）已是事实上的
身份判据（全库 CAS 惯例），把超长原文列留在索引里只是防哈希碰撞的双保险，
代价是长 URL 永远无法入库。约束改为 ``(tenant_id, canonical_url_hash)``；
两处写入路径的 ``ON CONFLICT`` 目标同步收窄
（``workflows/activities/collection.py``、``source_fetch.py``）。

注意：本迁移与 s19_0001/s20_0001（另一会话在途，生产未应用）同样以
s18_0003 为父，形成双 head；s19/s20 上线时需一次 ``alembic merge``。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s18_0004_url_hash_identity"
down_revision: str | Sequence[str] | None = "s18_0003_metrics_v2_failure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 幂等守卫：约束已是两列定义时整块跳过。
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='platform.source_url'::regclass
              AND conname='uq_source_url_identity'
              AND pg_get_constraintdef(oid) <> 'UNIQUE (tenant_id, canonical_url_hash)'
          ) THEN
            ALTER TABLE platform.source_url DROP CONSTRAINT uq_source_url_identity;
            ALTER TABLE platform.source_url ADD CONSTRAINT uq_source_url_identity
              UNIQUE (tenant_id, canonical_url_hash);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # 注意：库中若已存在 >2704 字节的 canonical_url，回退会因 btree 上限失败，
    # 属预期（回退即恢复"长 URL 不可入库"语义）。
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='platform.source_url'::regclass
              AND conname='uq_source_url_identity'
              AND pg_get_constraintdef(oid) = 'UNIQUE (tenant_id, canonical_url_hash)'
          ) THEN
            ALTER TABLE platform.source_url DROP CONSTRAINT uq_source_url_identity;
            ALTER TABLE platform.source_url ADD CONSTRAINT uq_source_url_identity
              UNIQUE (tenant_id, canonical_url_hash, canonical_url);
          END IF;
        END $$;
        """
    )
