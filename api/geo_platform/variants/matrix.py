"""W5 意图覆盖矩阵：意图×受众×地域×产品线 的格子坐标、覆盖判定与空格枚举。

轴口径（全部真实出处，缺省即"通用"桶，绝不臆造轴值）：
  * intent：六意图（textutil.INTENTS）；不可分类的现有 query 落"未分类"桶如实披露，
    不参与空格生成（对未分类空格生成变体等于瞎猜）；
  * audience：intake profile.audience_type；产品线：intake_promo(kind=product) 的 name，
    缺省时回退 asset 确认的 product_name；region：最新冻结配置 snapshot.regions。
  * 每个轴恒含 "通用" 桶（query 未显式锚定该轴时的归宿）。

现有 query 归格 = classify_intent + 各轴子串归因（轴值出现在文本中即锚定该值，
否则 "通用"）。空格 = 全笛卡尔积 - 已覆盖。矩阵规模硬上限 MAX_MATRIX_CELLS，
超限截断并 truncated=True 如实披露。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product

from .textutil import INTENTS, UNCLASSIFIED, classify_intent, normalize_query

GENERIC = "通用"
MAX_MATRIX_CELLS = 600


@dataclass(frozen=True)
class Cell:
    intent: str
    audience: str
    region: str
    product_line: str

    def as_dict(self) -> dict[str, str]:
        return {
            "intent": self.intent,
            "audience": self.audience,
            "region": self.region,
            "product_line": self.product_line,
        }

    def key(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class Axes:
    audiences: tuple[str, ...]
    regions: tuple[str, ...]
    product_lines: tuple[str, ...]

    def all_cells(self) -> list[Cell]:
        cells = [
            Cell(intent=intent, audience=audience, region=region, product_line=product_line)
            for intent, audience, region, product_line in product(
                INTENTS, self.audiences, self.regions, self.product_lines
            )
        ]
        return cells[:MAX_MATRIX_CELLS]

    @property
    def truncated(self) -> bool:
        return (
            len(INTENTS) * len(self.audiences) * len(self.regions) * len(self.product_lines)
            > MAX_MATRIX_CELLS
        )


def build_axes(audiences: list[str], regions: list[str], product_lines: list[str]) -> Axes:
    """轴值去空白去重并保持原序；每轴至少含 "通用"。"""

    def _clean(values: list[str]) -> tuple[str, ...]:
        seen: list[str] = []
        for value in values:
            value = value.strip()
            if value and value != GENERIC and value not in seen:
                seen.append(value)
        return (*seen, GENERIC)

    return Axes(
        audiences=_clean(audiences),
        regions=_clean(regions),
        product_lines=_clean(product_lines),
    )


def attribute_query(text: str, axes: Axes) -> Cell:
    """把一条 query 归入格子：意图走分类器；其余轴按轴值子串归因，缺省 "通用"。"""

    def _anchor(values: tuple[str, ...]) -> str:
        for value in values:
            if value != GENERIC and value in text:
                return value
        return GENERIC

    return Cell(
        intent=classify_intent(text, regions=axes.regions),
        audience=_anchor(axes.audiences),
        region=_anchor(axes.regions),
        product_line=_anchor(axes.product_lines),
    )


@dataclass(frozen=True)
class CoverageResult:
    total_cells: int
    covered_cells: int
    coverage_ratio: float
    truncated: bool
    unclassified_queries: tuple[str, ...]
    gaps: tuple[Cell, ...]


def compute_coverage(existing_texts: list[str], axes: Axes) -> CoverageResult:
    """现有 query 池的覆盖矩阵：已覆盖格子、空格清单、未分类桶（如实）。"""
    cells = axes.all_cells()
    covered: set[str] = set()
    unclassified: list[str] = []
    for text in existing_texts:
        if not normalize_query(text):
            continue
        cell = attribute_query(text, axes)
        if cell.intent == UNCLASSIFIED:
            unclassified.append(text)
            continue
        covered.add(cell.key())
    gaps = tuple(cell for cell in cells if cell.key() not in covered)
    return CoverageResult(
        total_cells=len(cells),
        covered_cells=len(cells) - len(gaps),
        coverage_ratio=round((len(cells) - len(gaps)) / len(cells), 6) if cells else 0.0,
        truncated=axes.truncated,
        unclassified_queries=tuple(unclassified),
        gaps=gaps,
    )


def duplicate_rate(texts: list[str], cluster_count: int) -> float:
    """近义重复率 = 1 - 簇数/条数（条数为 0 时返回 0.0，如实不除零）。"""
    total = len({normalize_query(text) for text in texts if normalize_query(text)})
    if total == 0:
        return 0.0
    return round(1 - cluster_count / total, 6)
