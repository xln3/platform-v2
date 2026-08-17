"""报告事实建议：报价单四指标（品牌提及率/推荐排名分布/Top1·Top3·Top5/竞品对比）
从分析链路自动生成 fact_rows 草稿，人工确认后走既有 POST /api/v2/reports 冻结。

数据通路（INV-1 口径延续：只读测量结果行，绝不触碰账号/profile 维）：
- 答案：``analytics.answer``（eligible AND NOT degraded = answer_agg_blind 语义，
  经 brandrank.service.fetch_answers 接缝；``model`` 列即平台、``region`` 即地域）；
- 品牌抽取：**只读** ``analytics.answer_brand_extract`` 的 status='ok' 行
  （fanout 落账，s06_0014）。本路径严禁调 LLM、不读文件缓存——同步 PG 聚合
  （毫秒级），未覆盖的 answer 诚实剔除并在 coverage 披露（INV-32 零合成）。

口径纪律：只取 brandrank 层（LLM 抽取 + 规则归并 + 双分母）；启发式层
mention_rate 不进报告。每行 method='brandrank-llm-v1' 标清。before/after
臂读取、臂记录构建、五指标聚合行构造已迁入 brandrank/compare.py（与
analytics comparisons 端点同一份代码，口径统一单点），本模块经 import 引用。

domain 真源 = ``project.brandrank_domain``（唯一，不回退缺省包）：未设置 →
DomainUnset（API 400 domain_unset，诚实，绝不静默用保险包）；值非法 →
UnknownDomain（400 unknown_domain）。

分组：平台×地域×query_text（报价单「每平台·每地域·每问题」口径）。每组内
分母 = 组内抽取覆盖条数（ok 行，含 brands=[] 的合法"未提及"），组间不合并；
组内计算走 domain/brandrank/metrics.py 纯函数（brand_special：提及率/avg_rank/
top_rates 双分母/竞品出现率）。

诚实边界：
- 窗内零 eligible 答案 / 零抽取覆盖 / 项目未配置品牌 → insufficient=true
  （insufficient_reasons 逐项披露），fact_rows 为空，绝不编造；
- 组数超 _MAX_GROUPS 防御性截断 → groups_truncated=true 披露；
- 目标品牌/竞品零提及 → 照常出 0 值行（分母真实），不是数据不足。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

from domain.brandrank import adapter, metrics
from domain.brandrank.rules import load_domain, normalize_brand
from domain.evidence.dlp import assert_secret_free

from ..analytics import service as analytics_service
from ..brandrank import compare as brandrank_compare
from ..brandrank import service as brandrank_service
from ..brandrank.compare import _fact_row, fetch_answers_window

# 单测接缝（test_report_before_after.py monkeypatch 本模块全局名）：before/after 路径
# 调用点把 fetch_answers_window 显式注入 compare 的臂构建函数，解析走本模块全局命名空间。
FACT_METHOD = brandrank_compare.FACT_METHOD
FACT_SOURCE = brandrank_compare.FACT_SOURCE
REPORT_TOP_NS = (1, 3, 5)  # 报价单口径：Top1/Top3/Top5 出现率
_MAX_COMPETITORS = 20  # 照 brandrank service 口径
# 防御性上限（真实窗 ~数十组），超出截断并 groups_truncated 披露；与 api-client
# 投影上限 5000 行对齐：200 组 × (5 目标行 + 20 竞品行) = 5000。
_MAX_GROUPS = 200

# ── 报价单服务 2/3/4 扩展 fact 组（W3 拉踩核查 / W2 官网能效 / 优化前后对比）──
# 扩展组一律进响应的独立键（w3_disparagement / w2_site_audit / before_after），
# 不进 fact_rows 主数组——api-client 投影对主数组词表 fail-closed（未知 metric
# 整响应判 unavailable），独立键对旧前端零影响，新前端自行投影扩展组。
W3_FACT_METHOD = "w3-disparagement-v1"
W2_FACT_METHOD = "w2-site-audit-v2"
_MAX_JUDGMENTS = 2000  # W3 窗级判定防御性上限（超出截断并披露）
_MAX_CASES = 20  # 典型案例上限（照 analytics cases 端点缺省 limit=20）
_MAX_SUGGESTIONS = 50  # 官网优化建议上限（T2 最新批次，超出截断并披露）
# W3 方向词表：target=被评对象 / subject=拉踩方（均先过 rules 归并再比对项目品牌/竞品）
DIRECTION_SMEAR_ON_OWN = "smear_on_own"  # 第三方/竞品 → 己方（抹黑己方）
DIRECTION_OWN_ON_COMPETITOR = "own_smear_on_competitor"  # 己方 → 竞品（己方拉踩竞品）


class ProjectNotFound(LookupError):
    """project 在本租户内不存在 → API 404 project_not_found（跨租户同 404）。"""


class DomainUnset(ValueError):
    """项目未设置 brandrank_domain → API 400 domain_unset（不回退缺省包）。"""


class UnknownDomain(ValueError):
    """真源列值非法（绕过 API 词表校验的直写）→ API 400 unknown_domain。"""


def _group_key(answer: dict[str, Any]) -> tuple[str, str, str]:
    """平台×地域×query 分组键；缺维如实空串（不臆造归属）。"""
    return (
        str(answer.get("model") or ""),
        str(answer.get("region") or ""),
        str(answer.get("query_text") or ""),
    )


def _section_unavailable(note: str) -> dict[str, Any]:
    """扩展组降级占位：数据面不可达（LookupError=tenant 上下文缺失，生产不可达——
    fetch_project 已先解析租户；只可能出现在全 fake 单测环境）时如实披露，
    绝不拖垮主草稿组，也绝不静默合成空数据。其余 DB 错误不上浮到这里（fail-loud）。"""
    return {"status": "unavailable", "insufficient_reasons": [note], "fact_rows": []}


def _section_guard(
    build: Callable[[], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """扩展组独立降级闸：只兜 LookupError（tenant 上下文缺失，见 _section_unavailable
    注释）；None（before/after 参数不齐）原样透传；其余异常照常上浮。"""
    try:
        return build()
    except LookupError:
        return _section_unavailable("tenant_context_missing")


def compute_report_fact_suggestions(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    window_days: int,
    before_start: str | None = None,
    before_end: str | None = None,
    after_start: str | None = None,
    after_end: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """窗内四指标 fact_rows 草稿（纯 PG 同步聚合，不调 LLM）。

    DB 读取全部走 brandrank.service 的接缝（fetch_project/fetch_answers/
    fetch_brand_extracts），与 brand-visibility 端点同一语义与单测 monkeypatch 点。

    扩展组（报价单服务 2/3/4）独立于主草稿计算、互不影响：
    - ``w3_disparagement``：拉踩/抹黑方向比率 + 典型案例（含契约表 T1 事实核查，
      表未就绪时 fact_check=null 优雅降级）；
    - ``w2_site_audit``：官网引用率/内容采纳率（契约 A1 三键 .get 容错）+
      官网优化建议（契约表 T2 最新批次，表未就绪时降级为空）；
    - ``before_after``：四参数（before_start/before_end/after_start/after_end，
      ISO 日期）齐全且合法时才计算，否则为 None（不产出该组，不报错）。
    """
    project = brandrank_service.fetch_project(dsn, tenant_pub_id, project_pub_id)
    if project is None:
        raise ProjectNotFound(project_pub_id)
    domain_value = (project.get("brandrank_domain") or "").strip()
    if not domain_value:
        raise DomainUnset(project_pub_id)
    try:
        rules = load_domain(domain_value)
    except ValueError as exc:
        raise UnknownDomain(str(exc)) from exc

    now = now or datetime.now(UTC)
    since = now - timedelta(days=window_days)
    window = {"start": since.isoformat(), "end": now.isoformat()}
    answers, truncated = brandrank_service.fetch_answers(dsn, tenant_pub_id, project_pub_id, since)
    table_rows = brandrank_service.fetch_brand_extracts(
        dsn, tenant_pub_id, [a["pub_id"] for a in answers], rules.domain
    )

    target_brand = project["brand_names"][0] if project["brand_names"] else None
    competitors = project["competitor_names"][:_MAX_COMPETITORS]

    # ── 分组：只收表内 ok 行（brands 形状校验与 brandrank service 同口径）──
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for answer in answers:
        table_row = table_rows.get(answer["pub_id"])
        if table_row is None or table_row.get("status") != "ok":
            continue
        brands = table_row.get("brands")
        if not (isinstance(brands, list) and all(isinstance(b, str) for b in brands)):
            continue  # ok 行但形状不符=未覆盖（诚实剔除）
        record = adapter.answer_to_brand_record(answer, list(brands))
        groups.setdefault(_group_key(answer), []).append(record)

    n_with_extract = sum(len(members) for members in groups.values())
    insufficient_reasons: list[str] = []
    if not answers:
        insufficient_reasons.append("no_answers")
    if answers and n_with_extract == 0:
        insufficient_reasons.append("no_extraction_coverage")
    if not target_brand:
        insufficient_reasons.append("target_brand_unset")
    insufficient = bool(insufficient_reasons)

    fact_rows: list[dict[str, Any]] = []
    groups_truncated = False
    if not insufficient:
        group_keys = sorted(groups)
        if len(group_keys) > _MAX_GROUPS:
            group_keys = group_keys[:_MAX_GROUPS]
            groups_truncated = True
        for key in group_keys:
            records = groups[key]
            n = len(records)  # 组内分母=抽取覆盖条数
            dimensions = {"platform": key[0], "region": key[1], "query": key[2]}
            special = metrics.brand_special(
                records, target_brand or "", rules=rules, total_count=n, top_ns=REPORT_TOP_NS
            )
            ranks = special["ranks"]
            # ① 品牌提及率
            fact_rows.append(
                _fact_row(
                    metric="brand_appearance_rate",
                    value=special["appearance_rate"],
                    unit="percent",
                    numerator=special["mentions"],
                    denominator=n,
                    dimensions=dimensions,
                    domain=rules.domain,
                    window=window,
                )
            )
            # ② 推荐排名分布（avg_rank 为汇总值，ranks 全量分布进 extra）
            fact_rows.append(
                _fact_row(
                    metric="rank_distribution",
                    value=special["avg_rank"],
                    unit="rank",
                    numerator=special["mentions"],
                    denominator=n,
                    dimensions=dimensions,
                    domain=rules.domain,
                    window=window,
                    extra={"best_rank": special["best_rank"], "ranks": ranks},
                )
            )
            # ③ Top1/Top3/Top5 出现率（双分母成对：value=of_total，of_mentions 进 extra）
            for top_n in REPORT_TOP_NS:
                rates = special["top_rates"][str(top_n)]
                fact_rows.append(
                    _fact_row(
                        metric=f"top{top_n}_appearance_rate",
                        value=rates["of_total"],
                        unit="percent",
                        numerator=sum(1 for r in ranks if r <= top_n),
                        denominator=n,
                        dimensions=dimensions,
                        domain=rules.domain,
                        window=window,
                        extra={"of_mentions": rates["of_mentions"]},
                    )
                )
            # ④ 竞品对比（每竞品一行；competitor=归并后品牌名）
            for competitor in competitors:
                comp = metrics.brand_special(
                    records, competitor, rules=rules, total_count=n, top_ns=REPORT_TOP_NS
                )
                fact_rows.append(
                    _fact_row(
                        metric="competitor_appearance_rate",
                        value=comp["appearance_rate"],
                        unit="percent",
                        numerator=comp["mentions"],
                        denominator=n,
                        dimensions=dimensions,
                        domain=rules.domain,
                        window=window,
                        extra={"competitor": comp["brand"]},
                    )
                )

    return {
        "project_pub_id": project_pub_id,
        "project_name": project["name"],
        "window_days": window_days,
        "window": window,
        "generated_at": now.isoformat(),
        "domain": rules.domain,
        "target_brand": target_brand,
        "competitors": competitors,
        "insufficient": insufficient,
        "insufficient_reasons": insufficient_reasons,
        "truncated": truncated,
        "groups_truncated": groups_truncated,
        "coverage": {
            "n_answers": len(answers),
            "n_with_extract": n_with_extract,
            "n_groups": len(groups),
            "n_fact_rows": len(fact_rows),
        },
        "fact_rows": fact_rows,
        # 扩展组（独立键；主数组形状零变化，旧前端投影不受影响）。逐组 LookupError
        # 降级（见 _section_unavailable），不拖垮主草稿。
        "w3_disparagement": _section_guard(
            lambda: _build_w3_section(
                dsn=dsn,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                rules=rules,
                project=project,
                domain=rules.domain,
                window=window,
                since=since,
                now=now,
            )
        ),
        "w2_site_audit": _section_guard(
            lambda: _build_w2_section(
                dsn=dsn,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                domain=rules.domain,
                window=window,
                since=since,
                now=now,
            )
        ),
        "before_after": _section_guard(
            lambda: _build_before_after_section(
                dsn=dsn,
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                rules=rules,
                target_brand=target_brand,
                domain=rules.domain,
                before_start=before_start,
                before_end=before_end,
                after_start=after_start,
                after_end=after_end,
            )
        ),
    }


# ══ 扩展组：DB 读取接缝（单测 monkeypatch 点；生产走真 PG）══════════════════
def fetch_disparagement_judgments(
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    since: datetime,
    until: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """W3 窗级判定（platform.disparagement_judgment，只取 judgment_status='ok'）。

    只读取项目目标品牌（platform.brand 首行）作为被核查对象的判定，历史竞品
    判定不进入报告工作室的风险统计与案例。
    窗=created_at ∈ [since, until]（与主建议的答案窗同一对起止）。出处链接：
    subject_type=source_document 时左联 source_document.url（照 analytics service
    .disparagement_cases 同款）；answer 判定的出处是答案本身（subject_pub_id）。
    服务 2 只统计本项目采集到的 AI 回答及其公开信源；不要求或混入不存在的
    客户“己方 GEO 稿件”。
    返回 (rows, truncated)：超 _MAX_JUDGMENTS 截断并置标记（诚实披露）。
    """
    with analytics_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
        rows = connection.execute(
            """
            SELECT j.pub_id AS judgment_pub_id, j.subject_type, j.subject_pub_id,
                   j.platform, j.subject_brand, j.target_brand, j.attitude,
                   j.disparagement, j.evidence_quote, j.confidence, j.method,
                   j.created_at, j.content_origin, d.url AS source_url
            FROM platform.disparagement_judgment j
            JOIN platform.project p ON p.id = j.project_id
            JOIN LATERAL (
              SELECT b.name
              FROM platform.brand b
              WHERE b.project_id = p.id
              ORDER BY b.created_at, b.pub_id
              LIMIT 1
            ) target ON target.name = j.target_brand
            LEFT JOIN platform.source_document d
              ON d.tenant_id = j.tenant_id AND d.pub_id = j.subject_pub_id
             AND j.subject_type = 'source_document'
            WHERE j.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              AND p.pub_id = %s
              AND j.content_origin = 'collection'
              AND j.judgment_status = 'ok'
              AND j.created_at >= %s AND j.created_at <= %s
            ORDER BY j.created_at DESC, j.pub_id
            LIMIT %s
            """,
            (project_pub_id, since, until, _MAX_JUDGMENTS + 1),
        ).fetchall()
    truncated = len(rows) > _MAX_JUDGMENTS
    return [dict(row) for row in rows[:_MAX_JUDGMENTS]], truncated


def fetch_disparagement_factchecks(
    dsn: str, tenant_pub_id: str, project_pub_id: str, judgment_pub_ids: list[str]
) -> dict[str, dict[str, Any]] | None:
    """契约表 T1（platform.disparagement_factcheck，Worker C 在建）逐条事实核查。

    返回 {judgment_pub_id: {verdict, summary, source_url}}；表不存在（Worker C
    未上线/迁移未跑）→ None——优雅降级：case 行 fact_check=null 且 section 披露
    fact_check_available=false，绝不 500。
    """
    if not judgment_pub_ids:
        return {}
    try:
        with analytics_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT judgment_pub_id, verdict, summary, source_url
                FROM platform.disparagement_factcheck
                WHERE project_pub_id = %s AND judgment_pub_id = ANY(%s::text[])
                """,
                (project_pub_id, judgment_pub_ids),
            ).fetchall()
    except psycopg.errors.UndefinedTable:
        return None
    return {row["judgment_pub_id"]: dict(row) for row in rows}


def fetch_source_audit_overview(
    dsn: str, tenant_pub_id: str, project_pub_id: str, start: date, end: date
) -> dict[str, Any]:
    """W2 信源审计总览读取接缝：直接复用 analytics 侧只读聚合（同一口径单点）。

    契约 A1：Worker B 将在返回值上新增 own_site_transcript_total /
    own_site_transcript_accurate / own_site_adoption_rate 三键；本侧一律 .get
    容错，缺键按 None 处理（降级为 adoption 行 value=null + note 披露）。
    """
    return analytics_service.AnalyticsService(dsn=dsn).source_audit_overview(
        tenant_pub_id=tenant_pub_id, project_pub_id=project_pub_id, start=start, end=end
    )


def fetch_site_audit_suggestions(
    dsn: str, tenant_pub_id: str, project_pub_id: str
) -> dict[str, Any] | None:
    """契约表 T2（platform.site_audit_suggestion，Worker C 在建）：最新批次全部行。

    返回 {"rows", "batch_pub_id", "truncated"}；无行 → rows=[] 且 batch_pub_id=None；
    表不存在 → None（优雅降级为空，section 披露 suggestions_available=false）。
    批次口径：created_at 最新一行所属 batch_pub_id 的全部行。
    """
    try:
        with analytics_service._platform_tenant_connection(dsn, tenant_pub_id) as connection:
            latest = connection.execute(
                """
                SELECT batch_pub_id FROM platform.site_audit_suggestion
                WHERE project_pub_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (project_pub_id,),
            ).fetchone()
            if latest is None:
                return {"rows": [], "batch_pub_id": None, "truncated": False}
            batch_pub_id = latest["batch_pub_id"]
            rows = connection.execute(
                """
                SELECT pub_id, category, severity, title, detail,
                       evidence_document_pub_id, model, created_at
                FROM platform.site_audit_suggestion
                WHERE project_pub_id = %s AND batch_pub_id = %s
                ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                         pub_id
                LIMIT %s
                """,
                (project_pub_id, batch_pub_id, _MAX_SUGGESTIONS + 1),
            ).fetchall()
    except psycopg.errors.UndefinedTable:
        return None
    truncated = len(rows) > _MAX_SUGGESTIONS
    return {
        "rows": [dict(row) for row in rows[:_MAX_SUGGESTIONS]],
        "batch_pub_id": batch_pub_id,
        "truncated": truncated,
    }


# fetch_answers_window / _arm_records / _fact_row 已迁入 brandrank/compare.py
# （本文件顶部 import 同名引用，单测 monkeypatch 本模块全局名仍生效）。


# ── 扩展组：输出清洗（照 analytics/router.py 同款：进报告的事实先过 DLP/URL 收窄）──
def _safe_optional_text(value: object, max_length: int = 1000) -> str | None:
    """非串/空/超长/DLP 命中 → None（照 analytics/router._safe_optional_text）。"""
    if not isinstance(value, str) or not value or len(value) > max_length:
        return None
    try:
        assert_secret_free(value)
    except ValueError:
        return None
    return value


def _safe_source_url(value: object) -> str | None:
    """只留 scheme://host[:port]/path（丢 query/fragment；照 analytics/router 同款）。"""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))


# ── 扩展组：W3 拉踩/抹黑核查（报价单服务2）──────────────────────────────────
def _direction_of(
    subject_std: str, target_std: str, own: set[str], competitors: set[str]
) -> str | None:
    """判定方向（归并后品牌名比对）：→己方=抹黑己方；己方→竞品=己方拉踩竞品；
    两侧都沾不上项目品牌 → None（如实计入 n_undirected 披露，不硬塞方向）。"""
    if target_std and target_std in own:
        return DIRECTION_SMEAR_ON_OWN
    if subject_std and subject_std in own and target_std in competitors:
        return DIRECTION_OWN_ON_COMPETITOR
    return None


def _build_w3_section(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    rules: Any,
    project: dict[str, Any],
    domain: str,
    window: dict[str, str],
    since: datetime,
    now: datetime,
) -> dict[str, Any]:
    """W3 风险核查组：disparagement_rate（按方向分组）+ disparagement_case（逐条）。

    分母口径：方向组内 judgment_status='ok' 判定总数（双分母注释见行 extra）；
    案例=disparagement=true 按 confidence 降序截断 _MAX_CASES 条，denominator=
    窗内 disparagement=true 判定总数（真实计数，披露于 section.n_disparagement）。
    """
    judgments, judgments_truncated = fetch_disparagement_judgments(
        dsn, tenant_pub_id, project_pub_id, since, now
    )
    own = {normalize_brand(b, rules) for b in project["brand_names"] if b}
    target_brand = (
        normalize_brand(str(project["brand_names"][0]), rules) if project["brand_names"] else ""
    )
    # DB 读取已经按目标品牌收紧；这里再做一次消费侧防御，避免滚动升级期间的旧
    # 读取实现或测试接缝把竞品判定混回客户风险统计。
    judgments = [
        judgment
        for judgment in judgments
        if normalize_brand(str(judgment.get("target_brand") or ""), rules) == target_brand
    ]
    competitor_set = {normalize_brand(c, rules) for c in project["competitor_names"] if c}

    buckets: dict[str, dict[str, int]] = {
        DIRECTION_SMEAR_ON_OWN: {"judgments": 0, "disparagement": 0},
        DIRECTION_OWN_ON_COMPETITOR: {"judgments": 0, "disparagement": 0},
    }
    n_undirected = 0
    cases: list[dict[str, Any]] = []
    for judgment in judgments:
        subject_std = normalize_brand(str(judgment.get("subject_brand") or ""), rules)
        target_std = normalize_brand(str(judgment.get("target_brand") or ""), rules)
        direction = _direction_of(subject_std, target_std, own, competitor_set)
        if direction is None:
            n_undirected += 1
        else:
            buckets[direction]["judgments"] += 1
            if judgment.get("disparagement"):
                buckets[direction]["disparagement"] += 1
        if judgment.get("disparagement"):
            cases.append({**judgment, "direction": direction})

    n_disparagement = len(cases)
    rows: list[dict[str, Any]] = []
    for direction in (DIRECTION_SMEAR_ON_OWN, DIRECTION_OWN_ON_COMPETITOR):
        bucket = buckets[direction]
        if bucket["judgments"] == 0:
            continue  # 该方向零判定 → 不出行（不编造 0 分母）
        rows.append(
            _fact_row(
                metric="disparagement_rate",
                value=round(bucket["disparagement"] / bucket["judgments"] * 100, 2),
                unit="percent",
                numerator=bucket["disparagement"],
                denominator=bucket["judgments"],
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W3_FACT_METHOD,
                extra={"direction": direction},
            )
        )

    # 典型案例：confidence 降序（None 垫底），输入已按 created_at DESC——稳定排序
    # 保同分时间序；逐条附 T1 事实核查（表未就绪 → fact_check=null 降级）。
    cases.sort(
        key=lambda j: float(j["confidence"]) if j.get("confidence") is not None else -1.0,
        reverse=True,
    )
    selected = cases[:_MAX_CASES]
    cases_truncated = len(cases) > _MAX_CASES
    factchecks = (
        fetch_disparagement_factchecks(
            dsn, tenant_pub_id, project_pub_id, [str(c["judgment_pub_id"]) for c in selected]
        )
        if selected
        else {}
    )
    for case in selected:
        factcheck = (factchecks or {}).get(case["judgment_pub_id"])
        fact_check = None
        if factcheck is not None:
            fact_check = {
                "verdict": _safe_optional_text(factcheck.get("verdict"), 40),
                "summary": _safe_optional_text(factcheck.get("summary"), 2000),
                "source_url": _safe_source_url(factcheck.get("source_url")),
            }
        rows.append(
            _fact_row(
                metric="disparagement_case",
                value=None,
                unit="case",
                numerator=1,
                denominator=n_disparagement,
                dimensions={"platform": str(case.get("platform") or ""), "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W3_FACT_METHOD,
                extra={
                    "judgment_pub_id": str(case["judgment_pub_id"]),
                    "direction": case["direction"],
                    "content_origin": _safe_optional_text(case.get("content_origin"), 20),
                    "subject_brand": _safe_optional_text(case.get("subject_brand"), 200),
                    "target_brand": _safe_optional_text(case.get("target_brand"), 200),
                    "evidence_quote": _safe_optional_text(case.get("evidence_quote"), 2000),
                    "confidence": (
                        float(case["confidence"]) if case.get("confidence") is not None else None
                    ),
                    "judge_method": _safe_optional_text(case.get("method"), 120),
                    "source_url": _safe_source_url(case.get("source_url")),
                    "answer_ref": (
                        _safe_optional_text(case.get("subject_pub_id"), 120)
                        if case.get("subject_type") == "answer"
                        else None
                    ),
                    "fact_check": fact_check,
                },
            )
        )

    return {
        "status": "ok" if judgments else "insufficient",
        "insufficient_reasons": [] if judgments else ["no_judgments"],
        "window": window,
        "n_judgments": len(judgments),
        "n_disparagement": n_disparagement,
        "n_undirected": n_undirected,
        "judgments_truncated": judgments_truncated,
        "cases_truncated": cases_truncated,
        # 无案例时不查 T1（None=未确认；有案例时为 True/False 如实披露）
        "fact_check_available": (factchecks is not None) if selected else None,
        "fact_rows": rows,
    }


# ── 扩展组：W2 官网引用能效（报价单服务3）──────────────────────────────────
def _build_w2_section(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    domain: str,
    window: dict[str, str],
    since: datetime,
    now: datetime,
) -> dict[str, Any]:
    """W2 官网能效组：报价指标 + 可观测辅助指标 + 优化建议。

    报价口径：
    - 官网引用率=至少引用一条官网 URL 的 AI 回答 / 全部合格 AI 回答；
    - 内容采纳率=经回答级判定确认已用于生成的回答 / 已评估回答。
      现有 transcript 只衡量引用转述与源文的一致性，必须单独披露，
      不得冒充采纳率。
    """
    overview = fetch_source_audit_overview(
        dsn, tenant_pub_id, project_pub_id, since.date(), now.date()
    )
    documents_total = int(overview.get("documents_total") or 0)
    own_site_documents = int(overview.get("own_site_documents") or 0)
    own_site_host = _safe_optional_text(overview.get("own_site_host"), 200)
    answers_total = int(overview.get("answers_total") or 0)
    own_site_cited_answers = int(overview.get("answers_with_own_site_citation") or 0)
    own_site_answer_citation_rate = overview.get("own_site_answer_citation_rate")

    rows: list[dict[str, Any]] = []
    insufficient_reasons: list[str] = []
    if answers_total > 0 and own_site_answer_citation_rate is not None:
        rows.append(
            _fact_row(
                metric="own_site_citation_share",
                value=round(float(own_site_answer_citation_rate) * 100, 2),
                unit="percent",
                numerator=own_site_cited_answers,
                denominator=answers_total,
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W2_FACT_METHOD,
                extra={
                    "own_site_host": own_site_host,
                    "definition": "answers_with_own_site_url / eligible_non_degraded_answers",
                },
            )
        )
    else:
        insufficient_reasons.append("no_eligible_answers")

    supplementary_specs = (
        (
            "answer_citation_coverage_rate",
            "citation_coverage_rate",
            "answers_with_citation",
            "answers_total",
        ),
        (
            "own_site_share_of_cited_answers",
            "own_site_share_of_cited_answers",
            "answers_with_own_site_citation",
            "answers_with_citation",
        ),
        (
            "own_site_reference_share",
            "own_site_reference_share",
            "own_site_citation_references",
            "citation_references_total",
        ),
        (
            "own_site_cited_text_evidence_rate",
            "own_site_cited_text_evidence_rate",
            "own_site_cited_text_answers",
            "answers_with_own_site_citation",
        ),
        (
            "own_site_fetched_document_share",
            "own_site_share",
            "own_site_documents",
            "documents_total",
        ),
        (
            "own_site_transcript_accuracy_rate",
            "own_site_transcript_accuracy_rate",
            "own_site_transcript_accurate",
            "own_site_transcript_total",
        ),
    )
    for metric, rate_key, numerator_key, denominator_key in supplementary_specs:
        rate = overview.get(rate_key)
        denominator = int(overview.get(denominator_key) or 0)
        if rate is None or denominator == 0:
            continue
        rows.append(
            _fact_row(
                metric=metric,
                value=round(float(rate) * 100, 2),
                unit="percent",
                numerator=int(overview.get(numerator_key) or 0),
                denominator=denominator,
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W2_FACT_METHOD,
                extra={"own_site_host": own_site_host, "supplementary": True},
            )
        )

    # 报价采纳率：只用回答级采纳判定的分子/分母。
    adoption_rate = overview.get("own_site_adoption_rate")
    adoption_evaluated = int(overview.get("own_site_adoption_evaluated_answers") or 0)
    adoption_verified = int(overview.get("own_site_adoption_verified_answers") or 0)
    if adoption_rate is None:
        note = (
            "adoption_metrics_unavailable"
            if "own_site_adoption_rate" not in overview
            else "no_answer_level_adoption_evaluations"
        )
        rows.append(
            _fact_row(
                metric="own_site_adoption_rate",
                value=None,
                unit="percent",
                numerator=adoption_verified,
                denominator=adoption_evaluated,
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W2_FACT_METHOD,
                extra={"insufficient": True, "note": note, "own_site_host": own_site_host},
            )
        )
    else:
        rows.append(
            _fact_row(
                metric="own_site_adoption_rate",
                value=round(float(adoption_rate) * 100, 2),
                unit="percent",
                numerator=adoption_verified,
                denominator=adoption_evaluated,
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W2_FACT_METHOD,
                extra={"own_site_host": own_site_host},
            )
        )

    suggestions = fetch_site_audit_suggestions(dsn, tenant_pub_id, project_pub_id)
    suggestion_rows = suggestions["rows"] if suggestions else []
    suggestion_batch = suggestions["batch_pub_id"] if suggestions else None
    for suggestion in suggestion_rows:
        rows.append(
            _fact_row(
                metric="site_audit_suggestion",
                value=None,
                unit="suggestion",
                numerator=1,
                denominator=len(suggestion_rows),
                dimensions={"platform": "", "region": "", "query": ""},
                domain=domain,
                window=window,
                method=W2_FACT_METHOD,
                extra={
                    "category": _safe_optional_text(suggestion.get("category"), 40),
                    "severity": _safe_optional_text(suggestion.get("severity"), 20),
                    "title": _safe_optional_text(suggestion.get("title"), 200),
                    "detail": _safe_optional_text(suggestion.get("detail"), 2000),
                    "evidence_document_pub_id": _safe_optional_text(
                        suggestion.get("evidence_document_pub_id"), 120
                    ),
                    "batch_pub_id": _safe_optional_text(suggestion_batch, 120),
                    "model": _safe_optional_text(suggestion.get("model"), 120),
                },
            )
        )

    return {
        "status": "ok" if rows else "insufficient",
        "insufficient_reasons": insufficient_reasons,
        "window": window,
        "own_site_host": own_site_host,
        "answers_total": answers_total,
        "answers_with_own_site_citation": own_site_cited_answers,
        "documents_total": documents_total,
        "own_site_documents": own_site_documents,
        "suggestions_available": suggestions is not None,
        "suggestion_batch_pub_id": suggestion_batch,
        "suggestions_truncated": (suggestions["truncated"] if suggestions else False),
        "fact_rows": rows,
    }


# ── 扩展组：优化前后对比（报价单服务 4）──────────────────────────────────────
def _parse_iso_date(value: str | None) -> date | None:
    """严格 YYYY-MM-DD；空/畸形 → None（调用方按「参数不齐不产出」处理，不报错）。"""
    if not value or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_before_after_section(
    *,
    dsn: str,
    tenant_pub_id: str,
    project_pub_id: str,
    rules: Any,
    target_brand: str | None,
    domain: str,
    before_start: str | None,
    before_end: str | None,
    after_start: str | None,
    after_end: str | None,
) -> dict[str, Any] | None:
    """优化前后对比组：双臂各自跑 metrics.analyze（同一把规则、同一目标品牌），
    产出 before_after_metric 行（value=after−before 差值，before/after/分母进 extra）。

    臂构建与五指标行构造在 brandrank/compare.py（与 analytics comparisons 端点
    同一份代码）；fetch_answers_window 经本模块全局名注入（单测接缝，见文件头
    import 注释）——对外行为与迁入前逐字段一致。

    与旧系统 build_before_after_compare 的口径差异（移植时有意的两处简化，留痕）：
    - 旧系统按单一 split_at 切分；本组由调用方显式给 before/after 两个闭区间日期窗
      （报价单「附录三选 3 题前后对比」口径：两次独立采集批次）， tick 不可解析
      不进任一侧的问题不存在（答案 capture_time 为 timestamptz 非空列）；
    - 旧系统 min_sample 阈值以下为不足；本组口径=臂内零 eligible 答案或零抽取
      覆盖即 insufficient（诚实占位，绝不伪零/伪差），有覆盖即出真实值。
    四参数不齐/畸形/前后倒置 → 返回 None（不产出该组，不报错）。
    """
    b_start, b_end = _parse_iso_date(before_start), _parse_iso_date(before_end)
    a_start, a_end = _parse_iso_date(after_start), _parse_iso_date(after_end)
    if b_start is None or b_end is None or a_start is None or a_end is None:
        return None
    if b_start > b_end or a_start > a_end:
        return None
    compare_window = {"start": b_start.isoformat(), "end": a_end.isoformat()}
    windows = {
        "before_start": b_start.isoformat(),
        "before_end": b_end.isoformat(),
        "after_start": a_start.isoformat(),
        "after_end": a_end.isoformat(),
    }

    before_answers, before_records, before_truncated = brandrank_compare._arm_records(
        dsn,
        tenant_pub_id,
        project_pub_id,
        domain,
        b_start,
        b_end,
        fetch_answers=fetch_answers_window,
    )
    after_answers, after_records, after_truncated = brandrank_compare._arm_records(
        dsn,
        tenant_pub_id,
        project_pub_id,
        domain,
        a_start,
        a_end,
        fetch_answers=fetch_answers_window,
    )
    coverage = {
        "before_answers": len(before_answers),
        "before_with_extract": len(before_records),
        "after_answers": len(after_answers),
        "after_with_extract": len(after_records),
        "before_truncated": before_truncated,
        "after_truncated": after_truncated,
    }

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
    if insufficient_reasons:
        return {
            "status": "insufficient",
            "insufficient_reasons": insufficient_reasons,
            "window": compare_window,
            "windows": windows,
            "coverage": coverage,
            "fact_rows": [],
        }

    rows = brandrank_compare.build_before_after_fact_rows(
        before_records,
        after_records,
        rules=rules,
        target_brand=target_brand or "",
        domain=domain,
        window=compare_window,
        windows=windows,
        top_ns=REPORT_TOP_NS,
    )

    return {
        "status": "ok",
        "insufficient_reasons": [],
        "window": compare_window,
        "windows": windows,
        "coverage": coverage,
        "fact_rows": rows,
    }
