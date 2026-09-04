"""合并点：s18_0004（source_url 长 URL 修复，生产线）与 s20_0001（manual 通道线）。

Revision ID: s21_0001_merge_heads
Revises: s18_0004_source_url_hash_identity, s20_0001_manual_channel

s18_0004_url_hash_identity 是生产应急修复（费列罗基线采集被 2718
字符百度追踪 URL 炸死），从生产 head s18_0003 直接分支；s19/s20 是另一会话
在途工作，同样挂在 s18_0003 之后。本合并迁移让工作树恢复单 head，无 schema
变更。生产部署 s19/s20 时按 alembic 正常多父升级即可。
"""

from collections.abc import Sequence

revision: str = "s21_0001_merge_heads"
down_revision: str | Sequence[str] | None = (
    "s18_0004_url_hash_identity",
    "s20_0001_manual_channel",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
