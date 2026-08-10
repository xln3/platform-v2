"""W3/W2 侧车重判（20260810）：LLM failover 修复后，对今日全部 6 个 run 重跑
source_audit（重判 llm_error 行已删）与 disparagement（词典兜底行已删）+ factcheck。

幂等：已判定行按 (doc/窗口, model, prompt_version) 跳过；本次用 LLM 真判。
env 提速：GEO_AUDIT_LLM_BASE_URL 指向直连可达的 inferera（aihubmix 本机直连不通，
failover 兜底逻辑已在代码里，脚本侧直接走可达通道省 7s/次的失败等待）。
"""

import asyncio
import sys

sys.path.insert(0, "api")
sys.path.insert(0, ".")

import temporalio.activity as _tactivity

_tactivity.heartbeat = lambda *args, **kwargs: None  # 脱离 activity 上下文

from workflows.activities.disparagement import (  # noqa: E402
    DisparagementInput,
    judge_run_disparagement,
)
from workflows.activities.disparagement_factcheck import (  # noqa: E402
    FactcheckInput,
    factcheck_disparagement_cases,
)
from workflows.activities.source_audit import SourceAuditInput, audit_run_sources  # noqa: E402

TENANT = "tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
RUNS = [
    "run_3SPWWSVZB71DJMM3MRHHWW3DVH",  # deepseek 首批 8
    "run_4C1P30Y1K4C3PNGJQAKPWSMPM6",  # doubao 首批 16
    "run_6QTYXW7JQPGYPBBSAGNKJBH4PT",  # yiyan 32
    "run_58D9PEJWG2ZSV5M80AH06YJ3YB",  # doubao 补采 bj 7
    "run_6Z62RVAMEXKWCXGHQQFR2QW1WM",  # doubao 补采 sh 9
    "run_2DZXRBD4V6N9VHJ5S84NT8879K",  # deepseek 补采 24
]


async def main() -> None:
    for run_pub_id in RUNS:
        print(f"== {run_pub_id}", flush=True)
        audit = await audit_run_sources(SourceAuditInput(TENANT, PROJECT, run_pub_id))
        print(
            f"   audit: audited={len(audit.audited)} skipped={len(audit.skipped)} "
            f"failures={len(audit.failures)} disabled={audit.disabled}",
            flush=True,
        )
        judge = await judge_run_disparagement(DisparagementInput(TENANT, PROJECT, run_pub_id))
        print(
            f"   disparagement: windows={judge.windows} judged={judge.judged} "
            f"dict_fallback={judge.dictionary_fallback} failures={len(judge.failures)}",
            flush=True,
        )
        factcheck = await factcheck_disparagement_cases(FactcheckInput(TENANT, PROJECT, run_pub_id))
        print(
            f"   factcheck: candidates={factcheck.candidates} checked={factcheck.checked} "
            f"supported={factcheck.supported} refuted={factcheck.refuted} "
            f"unverifiable={factcheck.unverifiable} llm_unavailable={factcheck.llm_unavailable}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
