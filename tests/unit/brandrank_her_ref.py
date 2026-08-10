"""对拍辅助（非测试文件本身）：从同事版源码 AST 提取她的函数本体做数值一致性对拍。

移植自旧库 server/tests/test_brandrank_her_ref.py（仅路径改为相对 platform-v2）。

为什么 AST 提取而不是 import：她的 analyze_brand.py / compare_zhongyi_analysis.py 导入期有
config_loader/openai/路径等副作用，直接 import 不可行。提取 FunctionDef 源码段 exec 到受控
命名空间（defaultdict/statistics/Counter + 她的字面量规则），得到**她的真实实现**，
与本包 metrics/rules 的输出逐案对拍——这是「逐公式对齐」的最强证据。

BRAND_MERGE_RULES 里 "中意人寿保险": TARGET_BRAND 是变量引用（analyze_brand L319-321），
TARGET_BRAND 取自她同目录 config.yaml 首个 ``target_brand:``（同旧库 extract_brand_rules.py）。
"""

from __future__ import annotations

import ast
import os
import re
import statistics
from collections import Counter, defaultdict

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HER_DIR = os.path.join(
    _HERE, "..", "..", "..", "GEO-auto-analysis", "data_analysis", "scripts", "tmp", "保险"
)
_ANALYZE_BRAND = os.path.join(_HER_DIR, "analyze_brand.py")
_COMPARE = os.path.join(_HER_DIR, "compare_zhongyi_analysis.py")


def _target_brand(src_dir: str) -> str:
    with open(os.path.join(src_dir, "config.yaml"), encoding="utf-8") as f:
        m = re.search(r"^\s*target_brand:\s*(\S+)\s*$", f.read(), re.MULTILINE)
    assert m, "她的 config.yaml 缺 target_brand"
    return m.group(1)


def _literal(tree: ast.Module, name: str, *, names: dict):
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ):
            continue
        if isinstance(node.value, ast.Dict):
            return {
                ast.literal_eval(k): (
                    names[v.id]
                    if isinstance(v, ast.Name) and v.id in names
                    else ast.literal_eval(v)
                )
                for k, v in zip(node.value.keys, node.value.values, strict=False)
            }
        return ast.literal_eval(node.value)
    raise AssertionError(f"assign not found: {name}")


def _exec_funcs(src_text: str, tree: ast.Module, func_names, namespace: dict) -> dict:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in func_names:
            seg = ast.get_source_segment(src_text, node)
            assert seg, f"source segment missing: {node.name}"
            exec(seg, namespace)  # noqa: S102 — 对拍用途，源是仓库外她的只读脚本
    missing = [n for n in func_names if n not in namespace]
    assert not missing, f"她的函数未提取到: {missing}"
    return namespace


def load_her_impl() -> dict:
    """返回 {normalize_brand, normalize_brand_list, collect_brand_ranks, merge_rank_maps,
    calculate_brand_ranking, calculate_appearance_rate, calculate_top_rate}——她的真实函数体。
    她的源码不在（其他机器/CI）→ skip 而非 fail。"""
    if not (os.path.isfile(_ANALYZE_BRAND) and os.path.isfile(_COMPARE)):
        pytest.skip("同事版 GEO-auto-analysis 源码不在本机")
    with open(_ANALYZE_BRAND, encoding="utf-8") as f:
        src_a = f.read()
    tree_a = ast.parse(src_a)
    tb = _target_brand(_HER_DIR)
    ns = {
        "defaultdict": defaultdict,
        "statistics": statistics,
        "Counter": Counter,
        "BRAND_MERGE_RULES": _literal(tree_a, "BRAND_MERGE_RULES", names={"TARGET_BRAND": tb}),
        "EXCLUDE_TERMS": _literal(tree_a, "EXCLUDE_TERMS", names={"TARGET_BRAND": tb}),
    }
    _exec_funcs(
        src_a,
        tree_a,
        {
            "normalize_brand",
            "normalize_brand_list",
            "collect_brand_ranks",
            "merge_rank_maps",
            "calculate_brand_ranking",
        },
        ns,
    )
    with open(_COMPARE, encoding="utf-8") as f:
        src_c = f.read()
    _exec_funcs(src_c, ast.parse(src_c), {"calculate_appearance_rate", "calculate_top_rate"}, ns)
    return ns
