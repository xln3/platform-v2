"""盛邦安全正式一轮（20260813）已采集未扇出答案补救。

沿革：0810 同款事故工具 remediate_failed_run_fanout_20260810.py 的正式一轮版。
本轮 9 个死亡 run（心跳超时事故/取消/pause 遗留）已完成 ~277 题但从未 fanout
（批返回才落库 + failed/cancelled 不触发 publish_downstream_event）。

口径（client-sbaq/新会话完整收口提示词_20260813.md §五）：
- 不改 run 状态（终态触发器 guard_collection_run_terminal_state，如实保留）；
- 按 publish_downstream_event 同款幂等 INSERT 逐题铸 answer_analysis 命令
  （ON CONFLICT (workflow_id) 空操作 + payload 漂移校验）；
- 再按序补 W2/W3 侧车（source_fetch → source_audit → disparagement → factcheck，
  均按 run 幂等键）。分析工作流只读 collection_task 行，不读 run 状态。

与 0810 版差异：
- run 状态断言放宽到 failed/cancelled/running（本轮含 cancelled 与 pause 遗留的
  running 僵尸 run_3J895——pause signal 已 delivered、workflow 不再推进）；
- CLI 支持 --runs/--mint-only/--sidecars-only，供周期补救（待办 C）复用 mint 段。

用法（platform-v2 目录，env 经 tools/run_with_platform_env.sh 注入）：
  tools/run_with_platform_env.sh .venv/bin/python \
    tools/remediate_sbaq_formal_fanout_20260813.py [--mint-only] [--runs a,b,c]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC

sys.path.insert(0, "api")
sys.path.insert(0, ".")

# 侧车 activity 的默认 heartbeat 闭包调 temporalio activity.heartbeat——脚本脱离
# activity 上下文运行，猴补丁为 no-op（侧车逻辑不依赖 heartbeat 返回值）。
import temporalio.activity as _tactivity
from sqlalchemy import select, text

from api.geo_platform.collection.models import CollectionRun, CollectionTask
from api.geo_platform.collection.run_service import _task_matrix
from api.geo_platform.config import get_settings
from api.geo_platform.projects.models import Brand, Competitor, MonitoringConfigVersion, Project
from api.geo_platform.tenancy.repository import TenantRepository
from workflows.activities.collection import WorkerSessionLocal, _analysis_dimensions
from workflows.activities.disparagement import DisparagementInput, judge_run_disparagement
from workflows.activities.disparagement_factcheck import (
    FactcheckInput,
    factcheck_disparagement_cases,
)
from workflows.activities.source_audit import SourceAuditInput, audit_run_sources
from workflows.activities.source_fetch import SourceFetchInput, fetch_run_sources

_tactivity.heartbeat = lambda *args, **kwargs: None

TENANT = "tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"

# 20260813 台账死亡 run（已扇出的 run_1523/run_38MVD 与 0 产出的六路除外）。
# 状态与已完成题数已于 2026-08-13 11:40 CST 经生产库核对。
RUNS = [
    "run_54V5F2GXY8Q5RGMYTP2VZA4PE7",  # cancelled, deepseek 94（B1r）
    "run_3J895WRN5MGF6CQFXVZ370MR50",  # running(pause 遗留，勿恢复), deepseek 52（B2）
    "run_6S4BB998KC54SNJN83B7BNCB6C",  # cancelled, yiyan 33（Y1）
    "run_447S4GAZ6SQVDCV8A1Y2PMR8ZN",  # cancelled, doubao 11（D1）
    "run_5A9D9TN0P0XWWRK88XZ8VFGXG3",  # cancelled, doubao+yiyan 28（A1r2）
    "run_3E1JYSJQ46FEKT90HGBZZBQZX4",  # failed, doubao+yiyan 40（A2）
    "run_222DHN1QWYK4Z7NM509BW4M9GX",  # failed, doubao+yiyan 12（A1r，DLP 误杀）
    "run_0JW1HZM4A9W6AJVKJ7BJSCRE4V",  # failed, yiyan 2（A1）
    "run_73BT4RAFC20JEA9G49B05V5EVZ",  # failed, deepseek 5（B1，stale 围栏）
]

# running 仅接纳 pause 遗留僵尸（workflow 不再推进）；真正在跑的 run mint 也是幂等无害。
# completed/completed_with_failures 正常扇出过，ON CONFLICT 空操作，重复 mint 零副作用。
_REMEDIABLE_STATES = {
    "failed",
    "cancelled",
    "running",
    "paused",
    "completed",
    "completed_with_failures",
}


def mint_analysis_commands(run_pub_id: str) -> str:
    """publish_downstream_event 的逐题铸命令部分（不带 run 状态门/outbox 事件）。"""
    with WorkerSessionLocal() as session:
        TenantRepository(session, TENANT)
        run = session.scalar(select(CollectionRun).where(CollectionRun.pub_id == run_pub_id))
        assert run is not None, run_pub_id
        if run.state not in _REMEDIABLE_STATES:
            return f"skip state={run.state}（非补救对象）"
        project = session.get(Project, run.project_id)
        config_version = session.get(MonitoringConfigVersion, run.config_version_id)
        assert project is not None and config_version is not None
        brand = session.scalar(
            select(Brand)
            .where(Brand.project_id == run.project_id)
            .order_by(Brand.created_at, Brand.pub_id)
        )
        assert brand is not None, "missing_brand"
        competitors = list(
            session.scalars(
                select(Competitor)
                .where(Competitor.project_id == run.project_id)
                .order_by(Competitor.created_at, Competitor.pub_id)
            )
        )
        task_by_key = {item.business_key: item for item in _task_matrix(config_version)}
        completed = list(
            session.scalars(
                select(CollectionTask)
                .where(CollectionTask.run_id == run.id, CollectionTask.state == "completed")
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        minted = 0
        _already = 0
        skipped = 0
        for task in completed:
            task_input = task_by_key.get(task.business_key)
            if task_input is None or task.answer_text is None:
                skipped += 1
                continue
            analysis_workflow_id = f"answer-analysis/{TENANT}/{run_pub_id}/{task.pub_id}"
            payload = {
                "persist": True,
                "tenant_pub_id": TENANT,
                "project_pub_id": project.pub_id,
                "answer_pub_id": task.pub_id,
                "text": task.answer_text,
                "brand": brand.name,
                "competitors": [item.name for item in competitors],
                "citations": json.loads(task.citations_json or "[]"),
                "search_queries": json.loads(task.search_queries_json or "[]"),
                "dimensions": _analysis_dimensions(
                    task_input,
                    run_pub_id=run_pub_id,
                    config_version_pub_id=config_version.pub_id,
                    browser_instance=(
                        json.loads(task.matrix_json or "{}").get("browser_instance") or None
                    ),
                ),
                "own_domains": [brand.website] if brand.website else [],
                "adapter_version": task_input.adapter,
                "capture_time": task.created_at.astimezone(UTC).isoformat(),
                "channel": "api",
                "access_class": "customer_private",
                "scorer_version": "scorer-v2",
                "metric_version": "metrics-v2",
                "model_version": "rules-v1",
            }
            persisted = session.execute(
                text(
                    """
                    INSERT INTO integration.workflow_start_command (
                      command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,
                      payload,trace_context
                    ) VALUES (
                      :command_id,:tenant_pub_id,'answer_analysis',:workflow_id,
                      :task_queue,CAST(:payload AS jsonb),'{}'::jsonb
                    )
                    ON CONFLICT (workflow_id)
                    DO UPDATE SET workflow_id=integration.workflow_start_command.workflow_id
                    RETURNING payload
                    """
                ),
                {
                    "command_id": uuid.uuid4(),
                    "tenant_pub_id": TENANT,
                    "workflow_id": analysis_workflow_id,
                    "task_queue": get_settings().s02_temporal_task_queue,
                    "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
            ).scalar_one()
            if persisted != payload:
                raise RuntimeError(f"payload drift on {analysis_workflow_id}")
            minted += 1
        session.commit()
        return f"minted_or_confirmed={minted} skipped={skipped} (completed={len(completed)})"


async def run_sidecars(run_pub_id: str) -> None:
    fetch = await fetch_run_sources(SourceFetchInput(TENANT, PROJECT, run_pub_id))
    print("   source_fetch:", fetch, flush=True)
    audit = await audit_run_sources(SourceAuditInput(TENANT, PROJECT, run_pub_id))
    print("   source_audit:", audit, flush=True)
    judge = await judge_run_disparagement(DisparagementInput(TENANT, PROJECT, run_pub_id))
    print("   disparagement:", judge, flush=True)
    factcheck = await factcheck_disparagement_cases(FactcheckInput(TENANT, PROJECT, run_pub_id))
    print("   factcheck:", factcheck, flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default=None, help="逗号分隔 run_pub_id 覆盖默认台账")
    parser.add_argument("--mint-only", action="store_true", help="只铸 answer_analysis 命令")
    parser.add_argument("--sidecars-only", action="store_true", help="只补 W2/W3 侧车")
    args = parser.parse_args()
    if args.mint_only and args.sidecars_only:
        raise SystemExit("--mint-only 与 --sidecars-only 互斥")
    runs = [item.strip() for item in args.runs.split(",")] if args.runs else RUNS
    for run_pub_id in runs:
        print(f"== {run_pub_id}", flush=True)
        if not args.sidecars_only:
            try:
                print("   fanout:", mint_analysis_commands(run_pub_id), flush=True)
            except RuntimeError as exc:
                # 周期模式（待办 C）不得因单个 run 漂移中断整批；漂移说明该 run
                # 的既有命令由旧版代码铸成，留待人工核查，如实记录继续。
                print(f"   fanout ERROR: {type(exc).__name__}", flush=True)
        if not args.mint_only:
            await run_sidecars(run_pub_id)


if __name__ == "__main__":
    asyncio.run(main())
