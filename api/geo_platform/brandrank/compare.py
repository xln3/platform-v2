"""brandrank 层对比共享模块：报告 before_after 扩展组 与 analytics comparisons 端点
的同一份口径实现（口径统一单点，严禁在别处再发明第二份）。

内容（从 reports/fact_suggestions.py 原样迁入的，标注 [迁入]；新增标注 [新增]）：
- [迁入] ``fetch_answers_window``：before/after 时间窗单臂 eligible 答案读取
  （eligible AND NOT degraded = answer_agg_blind 语义，INV-1 测量读纪律）；
- [迁入] ``_arm_records``：单臂 答案 → fanout 抽取 ok 行 → brandrank 记录
  （形状不符诚实剔除，INV-32 零合成）；
- [迁入] ``_fact_row``/``FACT_METHOD``/``FACT_SOURCE``：报告事实行信封——报告扩展组
  与 comparisons 端点产出**同一结构**的聚合行，行构造只此一份；
- [迁入] ``build_before_after_fact_rows``：双臂五指标（mention_rate/avg_rank/
  top1/3/5 of_total 口径，value=after−before）聚合行，逐字段与报告 before_after
  扩展组一致；
- [新增] ``fetch_answers_for_runs``：同谓词 + ``run_pub_id = ANY`` 的 run 组臂读取；
- [新增] ``pair_question_metrics``：逐题配对（query_text 为配对键——answer 表
  query_pub_id 写入路径不盖章，只能按文本对齐）；
- [新增] ``compute_run_comparison``：run_comparison 实体 → 现场计算结果编排。

诚实边界（与报告扩展组同一语义）：
- 臂内零 eligible 答案 / 零抽取覆盖 / 目标品牌未配置 → insufficient 诚实占位，
  绝不编造零值或伪差（INV-32）；
- 项目未设 brandrank_domain → DomainUnset（API 400 domain_unset，绝不静默回退
  缺省规则包）；真源值非法 → brandrank_service.UnknownDomain（400 unknown_domain）。

单测接缝（全部经模块全局名解析，monkeypatch 点名本模块或调用方模块属性即可）：
- ``fetch_answers_window``/``fetch_answers_for_runs``：臂构建函数的 ``fetch_answers``
  关键字参数缺省 None → 调用时解析本模块全局（调用方可显式注入自身全局名以保住
  既有接缝，见 reports/fact_suggestions.py 的 before/after 路径）；
- ``brandrank_service.fetch_project``/``fetch_brand_extracts``：经 service 模块属性
  调用（与 test_report_before_after.py / test_brandrank_api.py 同款）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from domain.brandrank import adapter, metrics
from domain.brandrank.rules import load_domain

from ..tenancy.psycopg import tenant_connection
from . import service as brandrank_service

# [迁入] 报告事实行信封常量（原 reports/fact_suggestions.py；行构造单点，见模块 docstring）
FACT_METHOD = "brandrank-llm-v1"
FACT_SOURCE = "system_computed"
COMPARE_TOP_NS = (1, 3, 5)  # 报价单口径：Top1/Top3/Top5 出现率
# [迁入] 优化前后对比指标词表（mention_rate=品牌提及率；topN=Top-N 出现率 of_total 口径，
# of_mentions 成对进 extra——双分母纪律与主建议一致）
BEFORE_AFTER_METRICS = ("mention_rate", "avg_rank", "top1", "top3", "top5")
# [迁入] before/after 单臂答案上限（照 brandrank service 口径）
_MAX_ARM_ANSWERS = 2000

# 臂记录构建的注入缝类型：与 fetch_answers_window/fetch_answers_for_runs 同返回形状
FetchAnswers = Callable[..., tuple[list[dict[str, Any]], bool]]


class DomainUnset(ValueError):
    """项目未设置 brandrank_domain → API 400 domain_unset（不回退缺省包；
    与 reports/fact_suggestions.DomainUnset 同语义，本层独立定义避免反向依赖）。"""


# [迁入] 报告事实行信封（原 fact_suggestions._fact_row，逐字段不变）
def _fact_row(
    *,
    metric: str,
    value: float | None,
    unit: str,
    numerator: int,
    denominator: int,
    dimensions: dict[str, str],
    domain: str,
    window: dict[str, str],
    method: str = FACT_METHOD,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric": metric,
        "value": value,
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "dimensions": dimensions,
        "source": FACT_SOURCE,
        "method": method,
        "domain": domain,
        "window": window,
    }
    if extra:
        row["extra"] = extra
    return row


# ══ 臂答案读取接缝 ══════════════════════════════════════════════════════════
# [迁入]（原 fact_suggestions.fetch_answers_window，逐行不变）
def fetch_answers_window(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    start: datetime,
    end: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """[start, end) 窗内 eligible 答案（eligible AND NOT degraded，与
    brandrank.service.fetch_answers 同口径 + 上界；before/after 双臂各自调用）。

    返回 (rows, truncated)：超 _MAX_ARM_ANSWERS 截断并置标记。
    """
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT pub_id, query_text, response_text, model, region, mode, capture_time
            FROM analytics.answer
            WHERE tenant_pub_id=%s AND project_pub_id=%s
              AND eligible AND NOT degraded
              AND capture_time >= %s AND capture_time < %s
            ORDER BY capture_time, pub_id
            LIMIT %s
            """,
            (tenant_pub_id, project_pub_id, start, end, _MAX_ARM_ANSWERS + 1),
        ).fetchall()
    truncated = len(rows) > _MAX_ARM_ANSWERS
    return [dict(row) for row in rows[:_MAX_ARM_ANSWERS]], truncated


# [新增] run 组臂读取：与 fetch_answers_window 同谓词同投影，窗条件换成 run 集合
def fetch_answers_for_runs(
    conn: psycopg.Connection[Any],
    tenant_pub_id: str,
    project_pub_id: str,
    run_pub_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], bool]:
    """指定 run 集合的 eligible 答案（eligible AND NOT degraded，与
    fetch_answers_window 同口径同投影；``run_pub_id = ANY`` 过滤）。

    conn 由调用方提供（tenant_connection 已置 app.tenant_pub_id、dict_row）——
    双臂共享同一连接/事务快照。返回 (rows, truncated)：超 _MAX_ARM_ANSWERS 截断。
    """
    if not run_pub_ids:
        return [], False
    rows = conn.execute(
        """
        SELECT pub_id, query_text, response_text, model, region, mode, capture_time
        FROM analytics.answer
        WHERE tenant_pub_id=%s AND project_pub_id=%s
          AND eligible AND NOT degraded
          AND run_pub_id = ANY(%s::text[])
        ORDER BY capture_time, pub_id
        LIMIT %s
        """,
        (tenant_pub_id, project_pub_id, list(run_pub_ids), _MAX_ARM_ANSWERS + 1),
    ).fetchall()
    truncated = len(rows) > _MAX_ARM_ANSWERS
    return [dict(row) for row in rows[:_MAX_ARM_ANSWERS]], truncated


# ══ 臂记录构建（答案 → 抽取 ok 行 → brandrank 记录）══════════════════════════
def _records_from_answers(
    dsn: str, tenant_pub_id: str, answers: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """答案集 → brandrank 记录：只收 fanout 表 ok 行（brands 形状校验与
    brandrank service 同口径），形状不符诚实剔除（INV-32 零合成）。"""
    table_rows = brandrank_service.fetch_brand_extracts(
        dsn, tenant_pub_id, [a["pub_id"] for a in answers], domain
    )
    records: list[dict[str, Any]] = []
    for answer in answers:
        table_row = table_rows.get(answer["pub_id"])
        if table_row is None or table_row.get("status") != "ok":
            continue
        brands = table_row.get("brands")
        if not (isinstance(brands, list) and all(isinstance(b, str) for b in brands)):
            continue  # ok 行但形状不符=未覆盖（诚实剔除）
        records.append(adapter.answer_to_brand_record(answer, list(brands)))
    return records


# [迁入]（原 fact_suggestions._arm_records；fetch_answers 注入缝是唯一的刻意增量——
# 缺省 None 时调用时解析本模块全局 fetch_answers_window，语义与原版逐行一致）
def _arm_records(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    domain: str,
    start_date: date,
    end_date: date,
    *,
    fetch_answers: FetchAnswers | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """单臂 [start_date, end_date]（含首尾日，UTC 日界）：eligible 答案 → fanout
    抽取 ok 行 → brandrank 记录（口径与主建议逐行一致：形状不符诚实剔除）。"""
    fetch = fetch_answers_window if fetch_answers is None else fetch_answers
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
    end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
    answers, truncated = fetch(dsn, tenant_pub_id, project_pub_id, start, end)
    records = _records_from_answers(dsn, tenant_pub_id, answers, domain)
    return answers, records, truncated


# [新增] run 组单臂构建：与 _arm_records 同口径，数据源换成 run 集合
def arm_records_for_runs(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    domain: str,
    run_pub_ids: Sequence[str],
    *,
    fetch_answers: FetchAnswers | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """单臂 run 集合：eligible 答案 → fanout 抽取 ok 行 → brandrank 记录
    （与 _arm_records 同一过滤/剔除口径）。"""
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        fetch = fetch_answers_for_runs if fetch_answers is None else fetch_answers
        answers, truncated = fetch(connection, tenant_pub_id, project_pub_id, run_pub_ids)
    records = _records_from_answers(dsn, tenant_pub_id, answers, domain)
    return answers, records, truncated


# ══ 五指标快照与聚合行（口径单点）════════════════════════════════════════════
def _metric_diff(before_value: float | None, after_value: float | None) -> float | None:
    """value=after−before（round 2）；任一臂 None（零提及 avg_rank）→ None（诚实空值）。"""
    return (
        round(after_value - before_value, 2)
        if before_value is not None and after_value is not None
        else None
    )


def _arm_metric_snapshot(
    records: list[dict[str, Any]],
    *,
    rules: Any,
    target_brand: str,
    top_ns: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    """单臂五指标快照：analyze(...)["target_brand"] → mention_rate/avg_rank/topN
    （of_total 口径；of_mentions 成对携带——值定义与报告 before_after 扩展组逐字一致）。

    调用方保证 target_brand 非空（否则 analyze 的 target_brand 为 None——与报告
    扩展组同一前置：target_brand_unset 先入 insufficient，不到这里）。
    """
    special = metrics.analyze(records, [], rules=rules, target_brand=target_brand, top_ns=top_ns)[
        "target_brand"
    ]
    snapshot: dict[str, dict[str, Any]] = {}
    for name in BEFORE_AFTER_METRICS:
        if name == "mention_rate":
            snapshot[name] = {
                "value": special["appearance_rate"],
                "unit": "percent",
                "numerator": special["mentions"],
                "denominator": len(records),
            }
        elif name == "avg_rank":
            snapshot[name] = {
                "value": special["avg_rank"],  # 零提及 → None（诚实空值）
                "unit": "rank",
                "numerator": special["mentions"],
                "denominator": len(records),
            }
        else:
            top_n = int(name[3:])
            rates = special["top_rates"][str(top_n)]
            snapshot[name] = {
                "value": rates["of_total"],  # 占总条数口径（与主建议一致）
                "unit": "percent",
                "numerator": sum(1 for r in special["ranks"] if r <= top_n),
                "denominator": len(records),
                "of_mentions": rates["of_mentions"],
            }
    return snapshot


# [迁入]（原 fact_suggestions._build_before_after_section 的聚合行构造段，逐字段不变：
# 同一 loop 同一 extra 键序——报告扩展组与 comparisons 端点的 aggregate 由此同构）
def build_before_after_fact_rows(
    before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]],
    *,
    rules: Any,
    target_brand: str,
    domain: str,
    window: dict[str, Any],
    windows: dict[str, Any],
    top_ns: tuple[int, ...] = COMPARE_TOP_NS,
) -> list[dict[str, Any]]:
    """双臂五指标 before_after_metric 聚合行（value=after−before 差值，
    before/after/分母进 extra；topN 双分母 of_total 为行值、of_mentions 成对进 extra）。"""
    before_snapshot = _arm_metric_snapshot(
        before_records, rules=rules, target_brand=target_brand, top_ns=top_ns
    )
    after_snapshot = _arm_metric_snapshot(
        after_records, rules=rules, target_brand=target_brand, top_ns=top_ns
    )
    denominators = {"before_n": len(before_records), "after_n": len(after_records)}

    rows: list[dict[str, Any]] = []
    for name in BEFORE_AFTER_METRICS:
        before_metric = before_snapshot[name]
        after_metric = after_snapshot[name]
        extra: dict[str, Any] = {
            "metric_name": name,
            "denominators": denominators,
            "windows": windows,
        }
        if "of_mentions" in before_metric:
            # 双分母成对披露（of_mentions=占提及条数），与主建议 topN 行同款
            extra["before_of_mentions"] = before_metric["of_mentions"]
            extra["after_of_mentions"] = after_metric["of_mentions"]
        rows.append(
            _fact_row(
                metric="before_after_metric",
                value=_metric_diff(before_metric["value"], after_metric["value"]),
                unit=after_metric["unit"],
                numerator=after_metric["numerator"],
                denominator=len(after_records),
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                extra={
                    **extra,
                    "before": before_metric["value"],
                    "after": after_metric["value"],
                    "before_numerator": before_metric["numerator"],
                },
            )
        )
    return rows


# ══ 逐题配对（[新增]：query_text 配对键——answer.query_pub_id 写入路径不盖章）════
def _queries_of(answers_or_records: list[dict[str, Any]], key: str) -> set[str]:
    return {str(item.get(key) or "") for item in answers_or_records}


def pair_question_metrics(
    baseline_records: list[dict[str, Any]],
    optimized_records: list[dict[str, Any]],
    *,
    rules: Any,
    target_brand: str,
    top_ns: tuple[int, ...] = COMPARE_TOP_NS,
    baseline_answers: list[dict[str, Any]] | None = None,
    optimized_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """逐题配对对比：两臂都出现的 query_text 各跑同一 analyze 管线（同 rules 同
    target_brand 同 top_ns），产出每题 before/after/delta（值定义与 before_after
    扩展组逐字一致）；只在一臂出现的进 unpaired。

    配对宇宙：给了 baseline_answers/optimized_answers（成对给）时按**答案**级
    query_text 取两臂交集——该题两臂都问过但某臂零抽取覆盖 → status='insufficient'
    诚实占位（该臂 before/after 为 None、delta 全 None，绝不伪零）；缺省按记录级
    分组（记录即覆盖，配对题两臂必有记录，零记录分支为纯防御）。
    """

    def by_query(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(str(record.get("query") or ""), []).append(record)
        return grouped

    baseline_by_query = by_query(baseline_records)
    optimized_by_query = by_query(optimized_records)
    if baseline_answers is not None and optimized_answers is not None:
        baseline_queries = _queries_of(baseline_answers, "query_text")
        optimized_queries = _queries_of(optimized_answers, "query_text")
    else:
        baseline_queries = set(baseline_by_query)
        optimized_queries = set(optimized_by_query)

    questions: list[dict[str, Any]] = []
    for query_text in sorted(baseline_queries & optimized_queries):
        before_subset = baseline_by_query.get(query_text, [])
        after_subset = optimized_by_query.get(query_text, [])
        insufficient_reasons: list[str] = []
        if not before_subset:
            insufficient_reasons.append("before_no_extraction_coverage")
        if not after_subset:
            insufficient_reasons.append("after_no_extraction_coverage")
        before_snapshot = (
            _arm_metric_snapshot(
                before_subset, rules=rules, target_brand=target_brand, top_ns=top_ns
            )
            if before_subset
            else None
        )
        after_snapshot = (
            _arm_metric_snapshot(
                after_subset, rules=rules, target_brand=target_brand, top_ns=top_ns
            )
            if after_subset
            else None
        )
        delta = {
            name: (
                _metric_diff(before_snapshot[name]["value"], after_snapshot[name]["value"])
                if before_snapshot is not None and after_snapshot is not None
                else None
            )
            for name in BEFORE_AFTER_METRICS
        }
        questions.append(
            {
                "query_text": query_text,
                "status": "insufficient" if insufficient_reasons else "ok",
                "insufficient_reasons": insufficient_reasons,
                "before": before_snapshot,
                "after": after_snapshot,
                "delta": delta,
            }
        )

    return {
        "questions": questions,
        "unpaired": {
            "baseline_only": sorted(baseline_queries - optimized_queries),
            "optimized_only": sorted(optimized_queries - baseline_queries),
        },
    }


# ══ run 组对比编排（[新增]：实体行 → 现场计算）════════════════════════════════
def _run_id_list(value: Any) -> list[str]:
    """jsonb 列 → text 数组（psycopg3 原生给 list；字符串/异常形状防御性解析，
    绝不臆造 run id）。"""
    if isinstance(value, str):
        value = json.loads(value)
    return [str(item) for item in value] if isinstance(value, list) else []


def compute_run_comparison(
    *,
    dsn: str,
    tenant_pub_id: str,
    comparison: dict[str, Any],
) -> dict[str, Any]:
    """run_comparison 实体行 → 现场计算结果（brandrank 层，与报告 before_after
    扩展组同一份管线：同臂过滤、同 analyze、同五指标行构造）。

    臂级 insufficient 语义与扩展组逐字一致（before/after_no_answers、
    before/after_no_extraction_coverage、target_brand_unset）→ status='insufficient'
    且 aggregate.metrics=[]（绝不伪零/伪差）；逐题配对照算（每题自带 status，
    单题 insufficient 诚实占位不影响他题）。
    """
    project_pub_id = str(comparison["project_pub_id"])
    project = brandrank_service.fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise brandrank_service.ProjectNotFound(project_pub_id)
    domain_value = (project.get("brandrank_domain") or "").strip()
    if not domain_value:
        raise DomainUnset(project_pub_id)
    try:
        rules = load_domain(domain_value)
    except ValueError as exc:
        raise brandrank_service.UnknownDomain(str(exc)) from exc
    target_brand = project["brand_names"][0] if project["brand_names"] else None

    baseline_run_ids = _run_id_list(comparison.get("baseline_run_pub_ids"))
    optimized_run_ids = _run_id_list(comparison.get("optimized_run_pub_ids"))
    before_answers, before_records, before_truncated = arm_records_for_runs(
        dsn, tenant_pub_id, project_pub_id, rules.domain, baseline_run_ids
    )
    after_answers, after_records, after_truncated = arm_records_for_runs(
        dsn, tenant_pub_id, project_pub_id, rules.domain, optimized_run_ids
    )
    coverage = {
        "before_answers": len(before_answers),
        "before_with_extract": len(before_records),
        "after_answers": len(after_answers),
        "after_with_extract": len(after_records),
        "before_truncated": before_truncated,
        "after_truncated": after_truncated,
    }

    # 臂级 insufficient 判定与报告 before_after 扩展组同一词表同一顺序
    insufficient_reasons: list[str] = []
    if not before_answers:
        insufficient_reasons.append("before_no_answers")
    elif not before_records:
        insufficient_reasons.append("before_no_extraction_coverage")
    if not after_answers:
        insufficient_reasons.append("after_no_answers")
    elif not after_records:
        insufficient_reasons.append("after_no_extraction_coverage")
    if not target_brand:
        insufficient_reasons.append("target_brand_unset")

    windows = {"baseline_run_pub_ids": baseline_run_ids, "optimized_run_pub_ids": optimized_run_ids}
    aggregate_metrics: list[dict[str, Any]] = []
    if not insufficient_reasons:
        aggregate_metrics = build_before_after_fact_rows(
            before_records,
            after_records,
            rules=rules,
            target_brand=target_brand or "",
            domain=rules.domain,
            window=windows,
            windows=windows,
        )

    # 逐题配对：答案级配对宇宙（某臂问过但零覆盖 → 单题 insufficient 诚实占位）；
    # 目标品牌未配置时品牌指标失去主体——questions 不落（绝不按空品牌名编造），
    # unpaired 仍按答案级如实列出。
    if target_brand:
        pairing = pair_question_metrics(
            before_records,
            after_records,
            rules=rules,
            target_brand=target_brand,
            top_ns=COMPARE_TOP_NS,
            baseline_answers=before_answers,
            optimized_answers=after_answers,
        )
    else:
        pairing = {
            "questions": [],
            "unpaired": {
                "baseline_only": sorted(
                    _queries_of(before_answers, "query_text")
                    - _queries_of(after_answers, "query_text")
                ),
                "optimized_only": sorted(
                    _queries_of(after_answers, "query_text")
                    - _queries_of(before_answers, "query_text")
                ),
            },
        }

    return {
        "status": "insufficient" if insufficient_reasons else "ok",
        "insufficient_reasons": insufficient_reasons,
        "domain": rules.domain,
        "target_brand": target_brand,
        "windows": windows,
        "coverage": coverage,
        "aggregate": {"metrics": aggregate_metrics},
        "questions": pairing["questions"],
        "unpaired": pairing["unpaired"],
    }
