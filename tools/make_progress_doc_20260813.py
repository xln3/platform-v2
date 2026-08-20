"""盛邦正式一轮采集进度文档生成（20260813）。

从 analytics.answer 按窗口（capture_time ≥ 2026-08-12T17:59Z，deep_think 模式）统计
136 问（34 组×4 表述）× 3 平台 × 2 地域的观测覆盖，产出 Markdown 进度表到 client-sbaq/。
列口径（用户指定）：平台、地域、重复遍数、测评账号、测评时间、模式。
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import psycopg
from launch_sbaq_formal_20260813 import ALL_GROUPS

WINDOW_START = "2026-08-12T17:59:00Z"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TENANT = "tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y"

LEGS = [
    ("豆包", "北京", "doubao_bj"),
    ("豆包", "上海", "doubao_sh"),
    ("DeepSeek", "北京", "deepseek_bj"),
    ("DeepSeek", "上海", "deepseek_sh"),
    ("文心一言", "北京", "yiyan_bj"),
    ("文心一言", "上海", "yiyan_sh"),
]
MODEL_OF = {"豆包": "doubao", "DeepSeek": "deepseek", "文心一言": "yiyan"}

# 测评账号（2026-08-13；昵称/手机号以页面实证为准，仅列已实证项；手机号打码）
ACCOUNTS = {
    "doubao_bj": "豆包账号 A（实例 doubao_bj）",
    "doubao_sh": "用户325066（实例 doubao_sh，页面实证）",
    "deepseek_bj": "DeepSeek 账号（实例 deepseek_bj）",
    "deepseek_sh": "155****2660（实例 deepseek_sh，页面实证）",
    "yiyan_bj": "geo测量26 / 155****2660（实例 yiyan_bj，账户文件实证）",
    "yiyan_sh": "文心账号（实例 yiyan_sh）",
}

VARIANT_LABELS = ["原词/优化句", "变体A", "变体B", "变体C"]


def main() -> None:
    dsn = (
        subprocess.check_output(
            [
                "sudo",
                "-n",
                "sed",
                "-n",
                "s/^GEO_POSTGRES_DSN=//p",
                "/etc/geo-platform-v2/platform.env",
            ]
        )
        .decode()
        .splitlines()[0]
        .replace("postgresql+psycopg://", "postgresql://")
    )

    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            select query_text, model, region, count(*), max(capture_time)
            from analytics.answer
            where tenant_pub_id=%s and project_pub_id=%s
              and mode='deep_think'
              and capture_time >= %s::timestamptz
            group by 1,2,3
            """,
            (TENANT, PROJECT, WINDOW_START),
        ).fetchall()

    cells: dict[tuple[str, str, str], tuple[int, datetime]] = {}
    for qt, model, region, n, latest in rows:
        cells[(qt, model, region)] = (n, latest)

    now = datetime.now().astimezone()
    out: list[str] = []
    out.append("# 盛邦安全 GEO 正式采集一轮 · 采集进度表\n")
    out.append(f"- 生成时间：{now:%Y-%m-%d %H:%M %Z}")
    out.append(
        f"- 统计口径：`capture_time ≥ {WINDOW_START}`（UTC 日界窗口）且 `mode=deep_think`"
        f"（本轮全为深思考模式；normal 模式不在本轮矩阵）"
    )
    out.append("- 目标矩阵：136 问（34 组×4 表述）× 3 平台 × 2 地域 × 2 次独立采样 = 1632 条回答")
    out.append("- 账号与实例对照（本轮每实例一账号，互不混用）：\n")
    out.append("| 平台×地域 | 采集实例 | 测评账号 | 模式 |")
    out.append("|---|---|---|---|")
    for plat, region, inst in LEGS:
        out.append(f"| {plat}×{region} | {inst} | {ACCOUNTS[inst]} | deep_think |")
    out.append("")
    out.append("## 总览（每格：重复遍数 / 最近测评时间；— = 尚无观测）\n")

    header = "| 附录 | 组 | 表述 | 问题 |" + "".join(f" {p}×{r} |" for p, r, _ in LEGS)
    sep = "|---|---|---|---|" + "---|" * len(LEGS)
    out.append(header)
    out.append(sep)

    leg_full = {inst: 0 for _, _, inst in LEGS}
    for gi, (_gname, questions) in enumerate(ALL_GROUPS, 1):
        appendix = "二" if gi <= 18 else "三"  # 报价单：附录二 18 组 + 附录三 16 组
        for vi, q in enumerate(questions):
            cells_txt = []
            for plat, region, inst in LEGS:
                hit = cells.get((q, MODEL_OF[plat], region))
                if hit is None:
                    cells_txt.append("—")
                else:
                    n, latest = hit
                    cells_txt.append(f"{n}遍 {latest.astimezone():%m-%d %H:%M}")
                    if n >= 2:
                        leg_full[inst] += 1
            out.append(
                f"| 附录{appendix} | G{gi:02d} | {VARIANT_LABELS[vi]} | {q} | "
                + " | ".join(cells_txt)
                + " |"
            )

    out.append("")
    out.append("## 双腿覆盖率（双观测齐全的 cell 数 / 136）\n")
    out.append("| 平台×地域 | 双观测齐全 cell | 有观测 cell |")
    out.append("|---|---|---|")
    for plat, region, inst in LEGS:
        any_c = 0
        for _, questions in ALL_GROUPS:
            for q in questions:
                if (q, MODEL_OF[plat], region) in cells:
                    any_c += 1
        out.append(f"| {plat}×{region} | {leg_full[inst]} | {any_c} |")

    dest = Path("/home/xln/geo-system/client-sbaq/采集进度_20260813.md")
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("written:", dest, "rows:", len(ALL_GROUPS) * 4)


if __name__ == "__main__":
    main()
