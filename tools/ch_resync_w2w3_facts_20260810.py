"""CH 事实表与 PG 权威行一次性对齐（20260810，W2/W3 重判收尾）。

背景：0805/0806 历史 run 的 W2/W3 重判只改了 PG（platform.source_audit /
platform.disparagement_judgment），CH 投影（geo_analytics.*_fact）残留：
- disparagement_fact：214 行 method='dictionary_experimental'（词典兜底时代，PG 已无对应行）；
- source_audit_fact：30 行陈旧（CH 停留 llm_error，PG 已重判 ok/validation_failure）
  + 5 个 pub_id 双份（重投影的新行与未清理的旧 llm_error 行并存）。

本脚本：按 PG 反连接删除幽灵/陈旧/重复行，再从 PG 回插当前值
（event_id='ch-resync-20260810-<pub_id>'，event_time=PG updated_at，诚实标记）。
PG 为权威读路径，本脚本只动 CH。

用法（secret 只走环境变量，不入文件）：
  GEO_PG_DSN=postgresql://... GEO_CH_PASSWORD=... python tools/ch_resync_w2w3_facts_20260810.py
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request

import psycopg

CH_URL = os.environ.get("GEO_CH_URL", "http://127.0.0.1:18124")
CH_USER = os.environ.get("GEO_CH_USER", "geo")
CH_PASSWORD = os.environ["GEO_CH_PASSWORD"]
PG_DSN = os.environ["GEO_PG_DSN"].replace("postgresql+psycopg://", "postgresql://", 1)
RESYNC_MARK = "ch-resync-20260810"


def ch_query(sql: str) -> str:
    req = urllib.request.Request(
        f"{CH_URL}/?user={CH_USER}&password={CH_PASSWORD}", data=sql.encode()
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode()


def ch_ids(sql: str) -> list[str]:
    out = ch_query(sql).strip()
    return out.split("\n") if out else []


def wait_mutations(table: str) -> None:
    for _ in range(120):
        pending = ch_query(
            "SELECT count() FROM system.mutations "
            f"WHERE database='geo_analytics' AND table='{table}' AND is_done=0"
        ).strip()
        if pending == "0":
            return
        time.sleep(2)
    raise SystemExit(f"mutation on {table} not done in 240s")


def sql_quote(values: list[str]) -> str:
    return ",".join(f"'{v}'" for v in values)


def main() -> None:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT pub_id FROM platform.disparagement_judgment")
        pg_disp = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT pub_id, prompt_version, audit_status, COALESCE(verdict,''), model"
            " FROM platform.source_audit"
        )
        pg_sa = {r[0]: r for r in cur.fetchall()}

    # ---- disparagement_fact：词典兜底幽灵
    # （PG 无对应行的全部 = dictionary_experimental，已核验 214=214）
    ch_disp = set(ch_ids("SELECT judgment_pub_id FROM geo_analytics.disparagement_fact"))
    disp_ghosts = sorted(ch_disp - pg_disp)
    bad = (
        ch_ids(
            "SELECT judgment_pub_id FROM geo_analytics.disparagement_fact "
            f"WHERE judgment_pub_id IN ({sql_quote(disp_ghosts)})"
            " AND method != 'dictionary_experimental'"
        )
        if disp_ghosts
        else []
    )
    if bad:
        raise SystemExit(
            f"拒绝删除：{len(bad)} 个幽灵行非 dictionary_experimental（{bad[:3]}…）——人工复核"
        )
    print(f"disparagement: {len(ch_disp)} CH 行, 幽灵 {len(disp_ghosts)}（全为词典兜底，PG 已核)")

    # ---- source_audit_fact：陈旧（同 pub_id 属性漂移）+ 重复（同 pub_id 多行）
    ch_sa_rows = (
        ch_query(
            "SELECT source_audit_pub_id, prompt_version, audit_status, verdict, model "
            "FROM geo_analytics.source_audit_fact "
            "ORDER BY source_audit_pub_id, prompt_version, audit_status"
        )
        .strip()
        .split("\n")
    )
    ch_sa: dict[str, list[tuple[str, ...]]] = {}
    for line in ch_sa_rows:
        parts = tuple(line.split("\t"))
        ch_sa.setdefault(parts[0], []).append(parts[1:])
    stale = sorted(
        pid
        for pid, rows in ch_sa.items()
        if pid in pg_sa and len(rows) == 1 and rows[0] != tuple(pg_sa[pid][1:])
    )
    dups = sorted(pid for pid, rows in ch_sa.items() if len(rows) > 1)
    ghosts = sorted(pid for pid in ch_sa if pid not in pg_sa)
    if ghosts:
        raise SystemExit(f"拒绝继续：source_audit 存在 PG 无对应行的幽灵 {ghosts}——人工复核")
    fix_ids = sorted(set(stale) | set(dups))
    print(
        f"source_audit: {len(ch_sa_rows)} CH 行, 陈旧 {len(stale)}, "
        f"重复 {len(dups)}, 待修复 pub_id {len(fix_ids)}"
    )

    # ---- 执行删除（ALTER TABLE mutation），等落盘后回插
    ch_query(
        "ALTER TABLE geo_analytics.disparagement_fact DELETE WHERE method='dictionary_experimental'"
    )
    if fix_ids:
        ch_query(
            "ALTER TABLE geo_analytics.source_audit_fact DELETE WHERE source_audit_pub_id "
            f"IN ({sql_quote(fix_ids)})"
        )
    wait_mutations("disparagement_fact")
    wait_mutations("source_audit_fact")
    print("删除 mutation 已完成")

    # ---- 从 PG 回插 source_audit 当前值
    if fix_ids:
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.pub_id, p.pub_id, r.pub_id, sd.pub_id, sa.pub_id,
                       sd.url, sd.host, sa.dimension, COALESCE(sa.verdict, ''),
                       sa.audit_status, sa.model, sa.prompt_version,
                       to_char(sa.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')
                FROM platform.source_audit sa
                JOIN platform.tenant t ON t.id = sa.tenant_id
                JOIN platform.project p ON p.id = sa.project_id
                JOIN platform.source_document sd ON sd.id = sa.source_document_id
                JOIN platform.collection_run r ON r.id = sd.run_id
                WHERE sa.pub_id = ANY(%s)
                ORDER BY sa.pub_id
                """,
                (fix_ids,),
            )
            rows = cur.fetchall()
        if len(rows) != len(fix_ids):
            raise SystemExit(
                f"PG 回插行数 {len(rows)} != 待修复 {len(fix_ids)}——中止，删除已生效需人工补"
            )
        tsv = "\n".join(
            "\t".join([*map(str, row[:12]), row[12], f"{RESYNC_MARK}-{row[4]}"]) for row in rows
        )
        ch_query(
            "INSERT INTO geo_analytics.source_audit_fact"
            " (tenant_pub_id, project_pub_id, run_pub_id,"
            " source_document_pub_id, source_audit_pub_id, url, host, dimension, verdict,"
            " audit_status, model, prompt_version, event_time, event_id) FORMAT TabSeparated\n"
            + tsv
        )
        print(f"source_audit: 回插 {len(rows)} 行（event_id 前缀 {RESYNC_MARK}）")

    # ---- 校验：CH == PG
    after_disp = int(ch_query("SELECT count() FROM geo_analytics.disparagement_fact").strip())
    after_disp_dict = int(
        ch_query(
            "SELECT count() FROM geo_analytics.disparagement_fact"
            " WHERE method='dictionary_experimental'"
        ).strip()
    )
    after_sa = ch_query(
        "SELECT prompt_version, audit_status, count() FROM geo_analytics.source_audit_fact "
        "GROUP BY 1,2 ORDER BY 3 DESC"
    ).strip()
    dup_left = ch_query(
        "SELECT count() FROM (SELECT source_audit_pub_id FROM geo_analytics.source_audit_fact "
        "GROUP BY 1 HAVING count()>1)"
    ).strip()
    print(f"校验 disparagement: {after_disp} 行（PG {len(pg_disp)}），词典残留 {after_disp_dict}")
    print(f"校验 source_audit 重复 pub_id 剩余: {dup_left}")
    print("校验 source_audit 分布:\n" + after_sa)
    if after_disp != len(pg_disp) or after_disp_dict != 0 or dup_left != "0":
        raise SystemExit("校验未过——人工复核")
    print("OK：CH 已与 PG 对齐")


if __name__ == "__main__":
    sys.exit(main())
