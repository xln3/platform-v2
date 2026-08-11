"""迁移 ck 约束命名守卫（防 20260812 二次包装坑复发）。

机制：NAMING_CONVENTION 的 ck 模板含 %(constraint_name)s（tenancy/database.py），
凡显式给终形名（ck_ 开头），CREATE 时会被再包一层模板落库为
ck_<表>_ck_<表>_…（超 63 字符还会截断+哈希），而 DROP 逐字——建删不对称，
全链 downgrade 必炸（实证与存量矫正见 migrations/versions/s06_0017 docstring）。

正确写法：词缀名（如 name="status"，落库即 ck_<表>_status）；终形名不符合
ck_<表>_ 前缀形态时（如 tenant_environment_ck），用 op.execute 裸 SQL 落库。
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).parents[2] / "migrations" / "versions"

# op.create_check_constraint("ck_...) 与 sa.CheckConstraint(..., name="ck_...") 两类终形名写法。
_PATTERNS = (
    re.compile(r"""create_check_constraint\(\s*["']ck_"""),
    re.compile(r"""CheckConstraint\([^)]*?name\s*=\s*["']ck_""", re.DOTALL),
)


def test_ck_constraint_names_use_suffix_not_final_form() -> None:
    offenders = [
        path.name
        for path in sorted(VERSIONS.glob("*.py"))
        if any(pattern.search(path.read_text(encoding="utf-8")) for pattern in _PATTERNS)
    ]
    assert not offenders, (
        "ck 命名约定会把显式终形名再包一层（落库 ck_<表>_ck_<表>_…），"
        "请改用词缀名（name 不带 ck_ 前缀）或 op.execute 裸 SQL；违规文件: " + ", ".join(offenders)
    )
