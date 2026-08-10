"""runB/runC 一次性补救（20260810 v2）：persist 层 bug（citations 超限 + 公开内容 DLP
误拦，均已修复）导致两个 run workflow 中途死亡、state='failed'，已完成题从未 fanout。

v2 变更：run 终态有 guard_collection_run_terminal_state 触发器（终态不可逆）——不改
状态（run 确实失败过，如实保留），改为直接按 publish_downstream_event 同款幂等 INSERT
逐题铸 answer_analysis 命令（ON CONFLICT (workflow_id) 空操作 + payload 漂移校验），
随后按序补跑 W2/W3 侧车（source_fetch → source_audit → disparagement → factcheck，
均按 run 幂等键）。分析工作流只读 collection_task 行，不读 run 状态。
"""

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
RUNS = ["run_3SPWWSVZB71DJMM3MRHHWW3DVH", "run_4C1P30Y1K4C3PNGJQAKPWSMPM6"]


def mint_analysis_commands(run_pub_id: str) -> str:
    """publish_downstream_event 的逐题铸命令部分（不带 run 状态门/outbox 事件）。"""
    with WorkerSessionLocal() as session:
        TenantRepository(session, TENANT)
        run = session.scalar(select(CollectionRun).where(CollectionRun.pub_id == run_pub_id))
        assert run is not None, run_pub_id
        assert run.state == "failed", f"{run_pub_id} state={run.state}（预期 failed，如实保留）"
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
        return f"minted={minted} skipped={skipped} (completed={len(completed)})"


async def main() -> None:
    for run_pub_id in RUNS:
        print(f"== {run_pub_id}")
        print("   fanout:", mint_analysis_commands(run_pub_id))
        fetch = await fetch_run_sources(SourceFetchInput(TENANT, PROJECT, run_pub_id))
        print("   source_fetch:", fetch)
        audit = await audit_run_sources(SourceAuditInput(TENANT, PROJECT, run_pub_id))
        print("   source_audit:", audit)
        judge = await judge_run_disparagement(DisparagementInput(TENANT, PROJECT, run_pub_id))
        print("   disparagement:", judge)
        factcheck = await factcheck_disparagement_cases(FactcheckInput(TENANT, PROJECT, run_pub_id))
        print("   factcheck:", factcheck)


if __name__ == "__main__":
    asyncio.run(main())
