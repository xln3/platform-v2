"""V2 analytics 行 → 同事版 brand_list 记录 / 信源记录 的适配层。

移植自已退役旧系统 geosys/brandrank/adapter.py（INV-1 口径延续：只读测量结果行，
绝不触碰账号/profile 维）。她的记录形状（analyze_brand.py L1256-1266 写入、L1076-1081 读取）::

    {"brands": [...], "query": ..., "thinking_mode": "快速"|"思考",
     "ip": "北京"|"上海", "rec_type": "公司"|"产品"|None}

字段映射（V2 analytics.answer 列 → 她的字段）：
- response_text → 抽品牌输入（extract 的输入，不在本层）
- model         → 平台/模型（V2 多平台；她的数据全是豆包无此维，作为附加维透出）
- region        → ip 维（**不**套用她的缺省 '北京'：region='' 就如实 ''，不臆造归属地）
- mode          → thinking_mode 词表映射：normal→'快速'、deep_think→'思考'
                  （V2 采集 mode 取值与旧库一致：workflows/activities/*_adapter.py
                  normal|deep_think。未知 mode 原样透出，诚实可见不静默归类）
- query_text    → query
- rec_type      → 以她的「公司 / 产品」两类为基础，补充保险品类词和综合咨询兜底；
                  “寿险公司”优先归公司，避免单纯命中“寿险”而误判成产品。

eligible 过滤=调用方 SQL 保证（analytics.answer WHERE eligible AND NOT degraded，
对应旧库 answer_agg_blind 视图语义），本层不再过滤。

信源记录（对齐 analyze_source.py calculate_statistics 的消费形状）::

    {"sitename": str, "url": str, "index": int}   # index=citation ordinal（1-based，权重 Σ1/index）

V2 信源=analytics.citation_fact（采集期逐题落库的引用事实），列 ordinal/host/
canonical_url/original_url/title——**无 sitename 列**（旧库 references_json 才有）。
sitename 口径：host 归一化（小写、去 www. 前缀）→ url 解析主机名 → '（未知）'，
绝不留空串污染 Counter。与旧库的差异仅「无 sitename 直接可用」，归一化规则逐行一致。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# mode → 她的 thinking_mode 词表（V2 采集 mode 取值 normal|deep_think，与旧库一致）
MODE_LABELS = {"normal": "快速", "deep_think": "思考"}

UNKNOWN_SITENAME = "（未知）"


def mode_label(mode: str) -> str:
    """normal→'快速'、deep_think→'思考'；未知值原样返回（不静默归类）。"""
    return MODE_LABELS.get(mode or "", mode or "")


def rec_type_from_query(query: str) -> str | None:
    """归并客户问题类型；公司意图优先，避免把“寿险公司”误判成产品。"""
    q = query or ""
    company_words = ("公司", "品牌", "机构", "险企")
    product_words = ("产品", "重疾险", "医疗险", "寿险", "年金险", "养老保险", "护理保险")
    if any(word in q for word in company_words) and "产品" not in q:
        return "公司"
    if any(word in q for word in product_words):
        return "产品"
    if any(word in q for word in company_words):
        return "公司"
    return None


def answer_to_brand_record(row: dict[str, Any], brands: list[str]) -> dict[str, Any]:
    """analytics.answer 行（dict）+ 抽出的品牌列表 → 她的 brand_list 记录（附加 provenance 维）。

    row 须含 pub_id/model/query_text/region/mode 键（service.fetch_answers 的投影）。"""
    mode_raw = row.get("mode") or ""
    return {
        # —— 她的字段（metrics 直接消费）——
        "brands": list(brands),
        "query": row.get("query_text") or "",
        "thinking_mode": mode_label(mode_raw),
        "ip": row.get("region") or "",
        "rec_type": rec_type_from_query(row.get("query_text") or ""),
        # —— V2 provenance（她的数据没有；分析分组/审计用，不影响她的口径）——
        "answer_pub_id": row.get("pub_id"),
        "engine": row.get("model") or "",
        "mode_raw": mode_raw,
    }


def _normalize_host(host: str) -> str:
    """host 归一化：小写、去 www. 前缀（sitename 缺失时的兜底站点名）。"""
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def citation_to_source_entry(row: dict[str, Any]) -> dict[str, Any]:
    """analytics.citation_fact 行 → {sitename, url, index}（index=ordinal，1-based）。

    sitename=host 归一化 → canonical/original url 解析主机名 → '（未知）'。
    ordinal 缺失/非正整数 → 该条仍进统计但 index 兜底为 1（权重最高，诚实可见：
    citation_fact.ordinal 由分析管线恒写 1-based，此分支纯防御）。"""
    url = (row.get("canonical_url") or row.get("original_url") or "").strip()
    sitename = (
        _normalize_host(row.get("host") or "")
        or _normalize_host(urlparse(url).hostname or "")
        or UNKNOWN_SITENAME
    )
    ordinal = row.get("ordinal")
    index = ordinal if isinstance(ordinal, int) and ordinal >= 1 else 1
    return {"sitename": sitename, "url": url, "index": index}
