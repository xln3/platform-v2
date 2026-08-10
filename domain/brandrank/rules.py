"""品牌合并/剔除规则：领域可插拔（rules_data/merge_rules_<domain>.json），默认保险。

逐行移植自已退役旧系统 server/geosys/brandrank/rules.py（2026-08-07 退役，只读参考）；
规则数据由旧系统 scripts/extract_brand_rules.py 从同事版 analyze_brand.py AST 提取
（BRAND_MERGE_RULES L91-772 / EXCLUDE_TERMS L779-791 / LLM prompt L877-890），
本包数据文件与旧库逐字节一致（md5 对拍）。

normalize 语义逐行对齐 analyze_brand.py:
- normalize_brand      L794-817（精确命中 → 模糊匹配：带 ·/(/（ 的 pattern 取 core 前缀、
                       core⊆brand 且 len(core)≥2 且 len(core)/len(brand)>0.3；否则 pattern⊆brand
                       且 len(brand)≥len(pattern) 且长度比≤2.5——防"中意"被"中意人寿"反向匹配）
- normalize_brand_list L839-850（合并后剔除 EXCLUDE_TERMS；**保序不去重**，与原脚本
                       基于 Counter 的计数口径一致；黑名单只作用于合并后统计，raw 不受影响）
- merge_brands         L820-836（去重保序变体；她 raw 路径的旧口径，本包未用，不移植）

唯一刻意偏差：空串/纯空白输入直接返回 ''（她的 L806 会对 len(brand)==0 除零崩溃；
'' 随后被 normalize_brand_list 的 `if nb` 过滤，语义等价于"丢弃"）。

V2 与旧库的差异（仅装载路径，语义零变化）：
- 数据目录 data/ → rules_data/（V2 包内命名）；
- 行业映射 INDUSTRY_DOMAIN 照旧库（旧库 20260724 起 fail-loud：行业有值但未映射 →
  ValueError，调用方映射 400，绝不静默回退保险包——律所客户拿保险包静默跑的事故根因）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_data")
DEFAULT_DOMAIN = "insurance"

# 客户行业（中文行业词）→ 规则包 domain。旧库生产事故根因：律所客户拿保险包静默跑——
# 行业有值但未映射 / 映射到的包不存在，都必须 fail-loud，绝不静默回退保险包。
INDUSTRY_DOMAIN = {"保险": "insurance", "法律": "legal", "网络安全": "cybersecurity"}


def domain_for_industry(industry: str | None) -> str:
    """客户行业 → 规则包 domain。空行业 → DEFAULT_DOMAIN（向后兼容：老客户未填行业）；
    行业有值但未映射 → ValueError（诚实报错，调用方 400，绝不静默回退保险包）。"""
    ind = (industry or "").strip()
    if not ind:
        return DEFAULT_DOMAIN
    if ind in INDUSTRY_DOMAIN:
        return INDUSTRY_DOMAIN[ind]
    raise ValueError(
        f"未知行业 {ind!r}：无法确定品牌规则包（已映射: {sorted(INDUSTRY_DOMAIN)}）；"
        "请先为该行业配置规则包或补充行业映射")


@dataclass(frozen=True)
class DomainRules:
    """一个领域的品牌规则包（不可变；merge_rules 保持 JSON 文件序=源 dict 插入序）。"""
    domain: str
    category: str                       # LLM prompt 的 {category}（保险="保险公司"，可配）
    merge_rules: dict[str, str]         # 产品名/别名 -> 标准公司名（有序：模糊匹配按此序优先命中）
    exclude_terms: frozenset[str]        # 合并后统计剔除的非主体噪声词
    prompt_template: str = ""           # 含 {category}/{reply_text} 占位符（str.replace 填充）
    system_message: str = ""
    llm_defaults: dict[str, Any] = field(default_factory=dict)  # temperature/response_format 等

    def render_prompt(self, reply_text: str, category: str | None = None) -> str:
        """填充她的 prompt 模板（analyze_brand.py L877-890；replace 而非 format——
        模板内含 JSON 示例花括号，str.format 会把它当字段）。"""
        return (self.prompt_template
                .replace("{category}", category or self.category)
                .replace("{reply_text}", reply_text))


@lru_cache(maxsize=8)
def load_domain(domain: str = DEFAULT_DOMAIN) -> DomainRules:
    """加载领域规则包。未知领域 → ValueError（诚实报错，绝不回落臆造规则）。"""
    path = os.path.join(_DATA_DIR, f"merge_rules_{domain}.json")
    if not os.path.isfile(path):
        raise ValueError(f"unknown brandrank domain: {domain!r}（缺 {path}）")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    llm = doc.get("llm") or {}
    return DomainRules(
        domain=doc["domain"],
        category=doc["category"],
        merge_rules=dict(doc["merge_rules"]),          # json 保序 → dict 保序
        exclude_terms=frozenset(doc["exclude_terms"]),
        prompt_template=llm.get("prompt_template", ""),
        system_message=llm.get("system_message", ""),
        llm_defaults={k: v for k, v in llm.items()
                      if k not in ("prompt_template", "system_message")},
    )


def available_domains() -> list[str]:
    """rules_data/ 下已落盘的领域列表（观测用）。"""
    out = []
    for fn in sorted(os.listdir(_DATA_DIR)):
        if fn.startswith("merge_rules_") and fn.endswith(".json"):
            out.append(fn[len("merge_rules_"):-len(".json")])
    return out


def normalize_brand(brand: str, rules: DomainRules) -> str:
    """标准化单个品牌名称（逐行对齐 analyze_brand.py L794-817）。"""
    brand = brand.strip()
    if not brand:
        return brand                     # 偏差说明见模块 docstring（她的实现此处会除零）

    if brand in rules.merge_rules:
        return rules.merge_rules[brand]

    # 模糊匹配
    for pattern, standard in rules.merge_rules.items():
        if '·' in pattern or '(' in pattern or '（' in pattern:
            core = pattern.split('·')[0].split('(')[0].split('（')[0]
            if core and core in brand and len(core) >= 2:
                ratio = len(core) / len(brand)
                if ratio > 0.3:
                    return standard
        else:
            # 修复模糊匹配逻辑：只有当brand至少和pattern一样长时才进行包含匹配
            # 这防止了"中意"被"中意人寿"规则反向匹配的问题
            if pattern in brand and len(brand) >= len(pattern):
                len_ratio = max(len(brand), len(pattern)) / min(len(brand), len(pattern))
                if len_ratio <= 2.5:
                    return standard

    return brand


def normalize_brand_list(brands: list[str], rules: DomainRules) -> list[str]:
    """标准化一条品牌列表，并剔除 EXCLUDE_TERMS 中的非保险公司噪声词。

    逐行对齐 analyze_brand.py L839-850：用于"合并后"统计，保持列表内的出现顺序
    （即排名顺序），**不做去重**，与原脚本基于 Counter 的计数口径保持一致。
    黑名单只作用于合并后统计，原始(raw)数据不受影响。"""
    out = []
    for b in brands:
        nb = normalize_brand(b, rules)
        if nb and nb not in rules.exclude_terms:
            out.append(nb)
    return out
