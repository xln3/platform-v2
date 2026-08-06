"""W5 覆盖矩阵单元测试：轴装配 / 归格 / 空格 / 覆盖率前后对比 / 重复率 / 模板渲染。"""

from __future__ import annotations

from geo_platform.variants import matrix, service
from geo_platform.variants.matrix import Cell


def _axes() -> matrix.Axes:
    return matrix.build_axes(
        audiences=["B2C个人消费者"], regions=["上海"], product_lines=["重疾险"]
    )


def test_build_axes_appends_generic_bucket() -> None:
    axes = _axes()
    assert axes.audiences == ("B2C个人消费者", "通用")
    assert axes.regions == ("上海", "通用")
    assert axes.product_lines == ("重疾险", "通用")
    assert axes.truncated is False


def test_attribute_query_anchors_substrings_and_intent() -> None:
    cell = matrix.attribute_query("上海重疾险推荐有哪些", _axes())
    assert cell == Cell(intent="推荐", audience="通用", region="上海", product_line="重疾险")


def test_compute_coverage_gaps_and_ratio() -> None:
    axes = _axes()
    # 6 意图 × 2 受众 × 2 地域 × 2 产品线 = 48 格。
    result = matrix.compute_coverage(["上海重疾险推荐有哪些"], axes)
    assert result.total_cells == 48
    assert result.covered_cells == 1
    assert result.coverage_ratio == round(1 / 48, 6)
    assert len(result.gaps) == 47
    assert result.unclassified_queries == ()


def test_compute_coverage_unclassified_bucket_is_honest() -> None:
    result = matrix.compute_coverage(["保险"], _axes())
    assert result.covered_cells == 0
    assert result.unclassified_queries == ("保险",)


def test_coverage_improves_after_adding_gap_variants() -> None:
    axes = _axes()
    pool = ["上海重疾险推荐有哪些"]
    before = matrix.compute_coverage(pool, axes)
    brand = "中意人寿"
    generated = [service.render_gap_variant(cell, brand) for cell in before.gaps[:10]]
    after = matrix.compute_coverage(pool + generated, axes)
    assert after.covered_cells > before.covered_cells
    # 每条模板变体恰好补一个新格子（模板文本归格必须落回被补格子）。
    assert after.covered_cells == before.covered_cells + 10
    assert after.coverage_ratio > before.coverage_ratio


def test_render_gap_variant_roundtrip_lands_in_its_cell() -> None:
    axes = _axes()
    cell = Cell(intent="口碑", audience="B2C个人消费者", region="通用", product_line="通用")
    text = service.render_gap_variant(cell, "中意人寿")
    assert text == "面向B2C个人消费者的中意人寿口碑怎么样"
    assert matrix.attribute_query(text, axes) == cell


def test_render_gap_variant_templates_per_intent() -> None:
    axes = _axes()
    for intent in ("推荐", "对比", "选购", "场景", "口碑", "地域"):
        cell = Cell(intent=intent, audience="通用", region="上海", product_line="重疾险")
        text = service.render_gap_variant(cell, "中意人寿")
        assert "重疾险" in text
        assert matrix.attribute_query(text, axes).intent == intent


def test_duplicate_rate() -> None:
    texts = ["重疾险推荐有哪些", "重疾险推荐有哪些吗", "医疗险怎么选", "重疾险口碑怎么样"]
    # 归一化去重后 3 条，聚类 3 簇 → 重复率 0。
    assert matrix.duplicate_rate(texts, 3) == 0.0
    # 若 3 条归一化文本只聚成 2 簇（两条近义被并）→ 1/3 重复。
    assert matrix.duplicate_rate(texts, 2) == round(1 - 2 / 3, 6)
    assert matrix.duplicate_rate([], 0) == 0.0
