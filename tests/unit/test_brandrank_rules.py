"""domain.brandrank.rules：保险/法律规则数据 sanity + normalize/exclude 语义与她的实现对拍。

移植自旧库 server/tests/test_brandrank_rules.py（import 路径换 V2，断言零变化）。
"""

import pytest
from brandrank_her_ref import load_her_impl

from domain.brandrank import rules as rules_mod
from domain.brandrank.rules import (
    DomainRules,
    available_domains,
    load_domain,
    normalize_brand,
    normalize_brand_list,
)


@pytest.fixture(scope="module")
def rules():
    return load_domain("insurance")


@pytest.fixture(scope="module")
def her():
    return load_her_impl()


# ── 数据文件 sanity（提取自她的 analyze_brand.py，与旧库数据逐字节一致）────────────
def test_insurance_data_sanity(rules):
    assert rules.domain == "insurance" and rules.category == "保险公司"
    assert len(rules.merge_rules) > 400  # 提取时实测 482 条（任务书 ~680 是约数）
    assert len(rules.exclude_terms) == 31
    # 她文件里的代表条目
    assert rules.merge_rules["超级玛丽15号"] == "君龙人寿"  # 产品→公司
    assert rules.merge_rules["中意人寿保险"] == "中意人寿"  # TARGET_BRAND 变量引用已解析
    assert rules.merge_rules["擎天柱11号"] == "中意人寿"
    assert "重疾险" in rules.exclude_terms and "支付宝" in rules.exclude_terms
    # prompt 模板两个占位符俱在（她的 f-string 变量）
    assert "{category}" in rules.prompt_template and "{reply_text}" in rules.prompt_template
    assert rules.system_message.startswith("你是一个专业的数据提取助手")
    assert rules.llm_defaults["temperature"] == 0
    assert rules.llm_defaults["response_format"] == "json_object"


def test_available_domains_and_unknown():
    assert "insurance" in available_domains()
    with pytest.raises(ValueError):
        load_domain("不存在的领域")  # 未知领域诚实报错，绝不回落臆造


def test_render_prompt(rules):
    p = rules.render_prompt("正文XYZ", category="养老保险公司")
    assert '关于"养老保险公司"的AI回复文本' in p
    assert "正文XYZ" in p and "{reply_text}" not in p and "{category}" not in p


# ── normalize 语义：锚点用例（她的注释案例）─────────────────────────────
def test_normalize_anchor_cases(rules):
    assert normalize_brand("超级玛丽15号", rules) == "君龙人寿"  # 精确命中
    assert normalize_brand("  中意  ", rules) == "中意人寿"  # strip + 精确
    # 她的防反向匹配：pattern 比 brand 长时绝不包含匹配（analyze_brand L810-811）
    assert normalize_brand("中宏", rules) == "中宏"
    # 带 ( 的 pattern 走 core 前缀匹配（L803-807）：core='友邦人寿' ⊆ brand，占比>0.3
    assert normalize_brand("友邦人寿(AIA)是外资", rules) == "友邦人寿"
    # 模糊包含 + 长度比≤2.5：'中意'(2字) ⊆ '中意人寿保险'(6字)，比 3.0>2.5 → 不命中该 pattern；
    # 但 '中意人寿保险' 本身是精确键 → 中意人寿
    assert normalize_brand("中意人寿保险", rules) == "中意人寿"
    assert normalize_brand("", rules) == ""  # 空串防御（她的实现此处除零）
    assert normalize_brand("完全无关的词", rules) == "完全无关的词"  # 未命中原样返回


def test_normalize_fuzzy_length_ratio_boundary():
    """合成规则验证 2.5 长度比边界（她的 L812-815）。"""
    r = DomainRules(
        domain="t", category="c", merge_rules={"甲乙": "甲乙公司"}, exclude_terms=frozenset()
    )
    assert normalize_brand("甲乙丙丁戊", r) == "甲乙公司"  # 5/2=2.5 ≤2.5 → 命中
    assert normalize_brand("甲乙丙丁戊己", r) == "甲乙丙丁戊己"  # 6/2=3.0 >2.5 → 不命中
    assert normalize_brand("甲", r) == "甲"  # brand 短于 pattern → 绝不反向命中


def test_normalize_brand_list_semantics(rules):
    """保序不去重、黑名单只在合并后剔除（她的 L839-850）。"""
    out = normalize_brand_list(
        ["超级玛丽15号", "重疾险", "中意人寿", "小青龙7号", "支付宝", "中意人寿"], rules
    )
    # 重疾险/支付宝=EXCLUDE 剔除；君龙人寿两次出现**不去重**；顺序=出现序
    assert out == ["君龙人寿", "中意人寿", "君龙人寿", "中意人寿"]


# ── 与她的真实函数全量对拍（最强证据；源码不在场时 skip）──────────────────────
def _battery(rules):
    keys = list(rules.merge_rules)
    cases = list(keys)  # 全部 482 个精确键
    for k in keys[::7]:  # 派生变体（模糊匹配路径）
        cases += [k + "保险", k + "的产品", k[:1], k + "x"]
    cases += ["", "  ", "重疾险", "支付宝", "完全无关", "中意", "中宏", "友邦人寿(AIA)在售"]
    return cases


def test_normalize_brand_matches_her_impl(rules, her):
    for b in _battery(rules):
        assert normalize_brand(b, rules) == her["normalize_brand"](b), f"brand={b!r}"


def test_normalize_brand_list_matches_her_impl(rules, her):
    battery = _battery(rules)
    lists = [battery[i : i + 5] for i in range(0, len(battery), 5)]
    for bl in lists:
        assert normalize_brand_list(bl, rules) == her["normalize_brand_list"](bl)


# ── 法律领域规则包（merge_rules_legal.json，生产观测提取）────────────────
@pytest.fixture(scope="module")
def legal():
    return load_domain("legal")


def test_legal_data_sanity(legal):
    assert legal.domain == "legal" and legal.category == "律师事务所"
    assert "legal" in available_domains()
    # prompt/system 结构照 insurance 版（占位符俱在，仅领域描述不同）
    assert "{category}" in legal.prompt_template and "{reply_text}" in legal.prompt_template
    assert "律师事务所" in legal.prompt_template
    assert legal.system_message.startswith("你是一个专业的数据提取助手")
    assert legal.llm_defaults["temperature"] == 0


def test_legal_target_brand_aliases(legal):
    """目标客户华夏汇鸿：观测到的别名形态全部归并到标准所名。"""
    std = "上海华夏汇鸿律师事务所"
    for alias in (
        "华夏汇鸿",
        "华夏汇鸿律所",
        "华夏汇鸿律师事务所",
        "上海华夏汇鸿律师事务所",
        "上海华夏汇鸿刑事律师",
        "许席禄",
        "许席禄律师",
    ):
        assert normalize_brand(alias, legal) == std, f"alias={alias!r}"


def test_legal_competitor_aliases(legal):
    """竞品别名/简称→观测到的完整所名（含模糊匹配路径）。"""
    for alias, std in (
        ("靖予霖", "上海靖予霖律师事务所"),
        ("博和汉商", "上海博和汉商律师事务所"),
        ("锦天城", "上海市锦天城律师事务所"),
        ("上海市锦天城律师事务所", "上海市锦天城律师事务所"),
        ("金杜", "金杜律师事务所"),
        ("国浩（上海）", "国浩律师（上海）事务所"),
        ("北京盈科（上海）律师事务所", "北京盈科（上海）律师事务所"),
    ):
        assert normalize_brand(alias, legal) == std, f"alias={alias!r}"


def test_legal_exclude_terms(legal):
    """观测到的非主体泛词：合并后统计剔除；raw 不受影响（保序不去重口径不变）。"""
    out = normalize_brand_list(["华夏汇鸿", "本地律所", "律所", "刑事律师", "锦天城"], legal)
    assert out == ["上海华夏汇鸿律师事务所", "上海市锦天城律师事务所"]
    for noise in ("多家律所", "专业刑事精品律所", "上海本土头部综合律所", "律师事务所"):
        assert noise in legal.exclude_terms


# ── 行业 → 规则包 domain（fail-loud 门）────────────────────────────────
def test_domain_for_industry():
    assert rules_mod.domain_for_industry("保险") == "insurance"
    assert rules_mod.domain_for_industry("法律") == "legal"
    assert rules_mod.domain_for_industry(None) == "insurance"  # 空行业=老客户兼容默认
    assert rules_mod.domain_for_industry("") == "insurance"
    assert rules_mod.domain_for_industry("  ") == "insurance"


def test_domain_for_industry_unknown_fails_loud():
    """未映射行业必须报错——绝不静默回退保险包（律所事故根因的门）。"""
    with pytest.raises(ValueError, match="未知行业"):
        rules_mod.domain_for_industry("餐饮")
