#!/usr/bin/env python3
"""采集账号占用租约清扫 CLI（采集账号占用模型，2026-09-01 起，s19_0001）。

回收「running 且（租约过期——NULL 存量行视同过期；或持有 run 已终态/不存在）」
的账号为 idle（reason=reservation_reaped:<成因>，写 state_transition 审计事件）。
captcha/muted/quota_exhausted/error 保护态一律不碰（清扫不是健康修复，人工
恢复走 POST /api/v2/collection-platform-accounts/{pub_id}/force-release）。

判定唯一真源 = ``AccountGovernor.reap_stale_reservations``（与派题链
resolve_collectable 的惰性回收同一份代码）。供人工/cron 执行；幂等，无回收
时零输出（stdout 只打回收明细，一行一条 JSON）。

用法::

    set -a; . /etc/geo-platform-v2/platform.env; set +a   # GEO_POSTGRES_DSN
    .venv/bin/python tools/reap_account_reservations.py [--dry-run]

退出码：0 正常完成（含零回收）；2 --dry-run 发现有可回收占用；3 DSN 未配/DB
异常。--dry-run 只打印不提交（回收明细的 former_run_pub_id/stale_reason 供
人工复核后再正式执行）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from geo_platform.collection.account_governor import AccountGovernor  # noqa: E402
from geo_platform.config import get_settings  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="reap stale collection account reservations")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印可回收明细，不提交（发现有回收 → exit 2）",
    )
    args = parser.parse_args()
    dsn = get_settings().postgres_dsn  # GEO_POSTGRES_DSN（geo 角色，脚本惯例）
    if not dsn:
        print("GEO_POSTGRES_DSN is not set", file=sys.stderr)
        return 3
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(dsn, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with session_factory() as session:
            reaped = AccountGovernor(session).reap_stale_reservations()
            for entry in reaped:
                print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
    except Exception as exc:  # noqa: BLE001 — CLI 配置/DB 门：如实失败
        print(f"reap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if args.dry_run and reaped:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
