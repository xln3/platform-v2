"""runB/C 24 题分析链重铸（20260810）：首轮直铸命令在缺 GEO_BROWSER_*/GEO_MEASUREMENT_*
env 的脚本进程里算 dimensions → geo_source=unverified → eligible=false，指标读路径排除。

处置 = 删除这 24 题在 analytics 的全部投影行（answer/analysis/citation/extract/
metric_trace/metric_daily，按 answer_pub_id 与 dimensions->>run_pub_id 精确圈定）+
删除旧命令行 → 用完整 worker env（platform.env + worker-adapters.env）重新铸命令，
outbox worker 重跑 AnswerAnalysisWorkflow 全量重算（幂等键均为内容派生，安全）。
"""

import os
import sys

sys.path.insert(0, "api")
sys.path.insert(0, ".")

import psycopg

from tools.remediate_failed_run_fanout_20260810 import mint_analysis_commands

RUNS = ("run_3SPWWSVZB71DJMM3MRHHWW3DVH", "run_4C1P30Y1K4C3PNGJQAKPWSMPM6")
PID = "prj_68ER9J6QBX054EAX52G7BEF7PH"


def main() -> None:
    dsn = os.environ["GEO_POSTGRES_DSN"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as c:
        task_pubs = [
            r[0]
            for r in c.execute(
                """SELECT t.pub_id FROM platform.collection_task t
                   JOIN platform.collection_run r ON r.id=t.run_id
                   WHERE r.pub_id = ANY(%s) AND t.state='completed'""",
                (list(RUNS),),
            ).fetchall()
        ]
        print("answers to re-mint:", len(task_pubs))
        n_daily = c.execute(
            """DELETE FROM analytics.metric_daily
               WHERE project_pub_id=%s AND dimensions->>'run_pub_id' = ANY(%s)""",
            (PID, list(RUNS)),
        ).rowcount
        n_trace = c.execute(
            """DELETE FROM analytics.metric_trace
               WHERE project_pub_id=%s AND dimensions->>'run_pub_id' = ANY(%s)""",
            (PID, list(RUNS)),
        ).rowcount
        n_cit = c.execute(
            "DELETE FROM analytics.citation_fact WHERE answer_pub_id = ANY(%s)", (task_pubs,)
        ).rowcount
        n_aa = c.execute(
            "DELETE FROM analytics.answer_analysis WHERE answer_pub_id = ANY(%s)", (task_pubs,)
        ).rowcount
        n_ext = c.execute(
            "DELETE FROM analytics.answer_brand_extract WHERE answer_pub_id = ANY(%s)", (task_pubs,)
        ).rowcount
        n_ans = c.execute(
            "DELETE FROM analytics.answer WHERE pub_id = ANY(%s)", (task_pubs,)
        ).rowcount
        n_cmd = c.execute(
            """DELETE FROM integration.workflow_start_command
               WHERE workflow_id LIKE 'answer-analysis/%%' AND (
                     workflow_id LIKE %s OR workflow_id LIKE %s)""",
            (f"%/{RUNS[0]}/%", f"%/{RUNS[1]}/%"),
        ).rowcount
        print(
            f"deleted: metric_daily={n_daily} metric_trace={n_trace} citation={n_cit} "
            f"analysis={n_aa} extract={n_ext} answer={n_ans} command={n_cmd}"
        )
        c.commit()

    for run_pub_id in RUNS:
        print(run_pub_id, "->", mint_analysis_commands(run_pub_id))


if __name__ == "__main__":
    main()
