"""Repair check-constraint names double-wrapped by the ck naming convention.

Revision ID: s06_0017
Revises: s06_0016

背景（20260812 实证）：NAMING_CONVENTION 的 ck 模板含 %(constraint_name)s
（api/geo_platform/tenancy/database.py），alembic/SQLAlchemy 在 CREATE 时会把
显式给的终形名再包一层（如 ck_disparagement_judgment_content_origin 落库为
ck_disparagement_judgment_ck_disparagement_judgment_con_efd7），而 DROP 逐字
使用给定名——create/drop 不对称导致存量库约束名错位、全链 downgrade 必炸。
s06_0001/s06_0014/s06_0015/s06_0016 的建约束写法已修为词缀名/裸 SQL（fresh
重放直接落终形名）；本迁移把已按旧路径应用过的存量库（dev/prod）里的错位名
一次性 RENAME 对齐到作者终形名。防御式存在性判断：fresh 重放库无错位名时
整体 no-op，重复执行幂等。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s06_0017"
down_revision: str | Sequence[str] | None = "s06_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (schema, table, 错位名, 作者终形名)——错位名取自 dev 库 pg_constraint 实值盘点。
RENAMES: tuple[tuple[str, str, str, str], ...] = (
    ("platform", "tenant", "ck_tenant_tenant_environment_ck", "tenant_environment_ck"),
    (
        "platform",
        "collection_run",
        "ck_collection_run_collection_run_source_ck",
        "collection_run_source_ck",
    ),
    (
        "posting",
        "batch",
        "ck_batch_posting_batch_approval_state_ck",
        "posting_batch_approval_state_ck",
    ),
    (
        "platform",
        "disparagement_judgment",
        "ck_disparagement_judgment_ck_disparagement_judgment_con_efd7",
        "ck_disparagement_judgment_content_origin",
    ),
    (
        "platform",
        "disparagement_factcheck",
        "ck_disparagement_factcheck_ck_disparagement_factcheck_verdict",
        "ck_disparagement_factcheck_verdict",
    ),
    (
        "platform",
        "site_audit_suggestion",
        "ck_site_audit_suggestion_ck_site_audit_suggestion_category",
        "ck_site_audit_suggestion_category",
    ),
    (
        "platform",
        "site_audit_suggestion",
        "ck_site_audit_suggestion_ck_site_audit_suggestion_severity",
        "ck_site_audit_suggestion_severity",
    ),
    (
        "analytics",
        "answer_brand_extract",
        "ck_answer_brand_extract_ck_answer_brand_extract_status",
        "ck_answer_brand_extract_status",
    ),
    (
        "analytics",
        "run_comparison",
        "ck_run_comparison_ck_run_comparison_baseline_runs_array",
        "ck_run_comparison_baseline_runs_array",
    ),
    (
        "analytics",
        "run_comparison",
        "ck_run_comparison_ck_run_comparison_optimized_runs_array",
        "ck_run_comparison_optimized_runs_array",
    ),
)

# 标识符全部为本文件常量，无注入面；直接用 f-string 拼接。
_TEMPLATE = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_namespace n ON n.oid = c.connamespace
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE n.nspname = '{schema}' AND t.relname = '{table}' AND c.conname = '{old}'
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_namespace n ON n.oid = c.connamespace
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE n.nspname = '{schema}' AND t.relname = '{table}' AND c.conname = '{new}'
  ) THEN
    ALTER TABLE {schema}."{table}" RENAME CONSTRAINT "{old}" TO "{new}";
  END IF;
END
$$;
"""


def _rename(schema: str, table: str, old: str, new: str) -> None:
    op.execute(_TEMPLATE.format(schema=schema, table=table, old=old, new=new))


def upgrade() -> None:
    for schema, table, old, new in RENAMES:
        _rename(schema, table, old, new)


def downgrade() -> None:
    # 单向矫正：不回滚改名。本迁移把存量库对齐到「修复后迁移链」的产物形态，
    # 逆改回错位名只会让 s06_0015 等 downgrade 的逐字 DROP 再次失靶
    # （20260812 实证： downgrade 链在 s06_0017 逆改名后断在 UndefinedObject）。
    pass
