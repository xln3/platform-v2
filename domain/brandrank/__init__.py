"""品牌可见度分析核（brandrank）：旧系统品牌排名口径（第二层分析）的 V2 移植。

子模块：
- rules    领域规则包（rules_data/merge_rules_<domain>.json，与旧库逐字节一致）
           + normalize/exclude 逐行对齐 + 行业→domain fail-loud 映射
- extract  LLM 品牌抽取（GEO_BRANDRANK_LLM_* 独立 env 族；httpx 传输，主备 failover；
           失败诚实 ExtractError，绝不编造）
- adapter  analytics.answer/citation_fact 行 → 她的 brand_list 记录 / 信源记录
- metrics  排名/出现率/双分母 top 率/目标·竞品专项/信源分析（分母全部真实参数化）
- cache    抽取结果文件缓存（键=domain+答案哈希；runtime/ 下，不落 PG）

REST 层在 api/geo_platform/brandrank/（service+router），挂载于 api/geo_platform/main.py。
"""
from __future__ import annotations

from .rules import DEFAULT_DOMAIN, DomainRules, available_domains, domain_for_industry, load_domain

__all__ = [
    "DEFAULT_DOMAIN",
    "DomainRules",
    "available_domains",
    "domain_for_industry",
    "load_domain",
]
