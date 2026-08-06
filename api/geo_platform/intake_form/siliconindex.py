"""SiliconIndex 只读适配（旧 server/geosys/siliconindex/catalog.py 读模型移植）。

口径：
  * 快照目录 = config ``siliconindex_snapshot_dir``；根下 ``CURRENT`` 指针文件指向具体快照子目录；
  * 目录不存在 / CURRENT 缺失 / JSON 损坏 → ``available=False`` 优雅降级，**绝不报错**；
  * 只读：不做 sync、不写任何绑定表；candidates/template-questions 一律 candidate_only 预览。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def normalize(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", value).casefold())


_SNAPSHOT_FILES = (
    "snapshot-meta",
    "brands",
    "mentions",
    "competitor-relations",
    "categories",
    "query-templates",
    "compliance-rules",
)


class SiliconIndex:
    """SiliconIndex 快照只读视图；任何加载失败都退化为 available=False 的空目录。"""

    def __init__(self, snapshot_dir: str | Path):
        self.available = False
        self.meta: dict[str, Any] = {}
        self.brands: list[dict[str, Any]] = []
        self.mentions: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.categories: list[dict[str, Any]] = []
        self.templates: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self._brands: dict[str, dict[str, Any]] = {}
        try:
            root = Path(snapshot_dir)
            if not root.is_dir():
                return
            pointer = root / "CURRENT"
            if pointer.exists():
                root = root / pointer.read_text(encoding="utf-8").strip()
            if not root.is_dir():
                return
            self.meta = self._load(root, "snapshot-meta", {})
            self.brands = self._load(root, "brands", [])
            self.mentions = self._load(root, "mentions", [])
            self.relations = self._load(root, "competitor-relations", [])
            self.categories = self._load(root, "categories", [])
            self.templates = self._load(root, "query-templates", [])
            self.rules = self._load(root, "compliance-rules", [])
            self._brands = {
                str(x["brand_id"]): x
                for x in self.brands
                if isinstance(x, dict) and x.get("brand_id")
            }
            self.available = bool(self._brands)
        except OSError:
            return

    @staticmethod
    def _load(root: Path, name: str, default: Any) -> Any:
        try:
            data = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            return data if isinstance(data, type(default)) else default
        except (OSError, UnicodeError, json.JSONDecodeError):
            return default

    # ── 品牌名匹配（canonical/display/english 名 + mention 文本，normalize 后全等）─────
    def resolve(self, name: str) -> dict[str, Any] | None:
        needle = normalize(name)
        if not needle:
            return None
        candidates: set[str] = set()
        for brand in self.brands:
            if not isinstance(brand, dict):
                continue
            for key in ("canonical_name", "display_name", "english_name"):
                if normalize(str(brand.get(key, ""))) == needle:
                    candidates.add(str(brand.get("brand_id")))
        for mention in self.mentions:
            if isinstance(mention, dict) and normalize(str(mention.get("text", ""))) == needle:
                candidates.add(str(mention.get("brand_id")))
        candidates.discard("None")
        if len(candidates) != 1:
            return None
        return self._brands.get(next(iter(candidates)))

    def _category_label(self, category_id: Any) -> str:
        for cat in self.categories:
            if not isinstance(cat, dict) or cat.get("category_id") != category_id:
                continue
            return next(
                (
                    str(x)
                    for x in (cat.get(k) for k in ("level_4", "level_3", "level_2", "level_1"))
                    if x
                ),
                "",
            )
        return ""

    def category_path(self, brand: dict[str, Any]) -> list[str]:
        """主分类的 level_1..level_4 路径（过滤空层）。"""
        for cat in self.categories:
            if not isinstance(cat, dict) or cat.get("category_id") != brand.get(
                "primary_category_id"
            ):
                continue
            return [
                str(x)
                for x in (cat.get(k) for k in ("level_1", "level_2", "level_3", "level_4"))
                if x
            ]
        return []

    def mention_rules(self, brand_id: str) -> list[dict[str, Any]]:
        """reviewed/active 状态的 mention 规则（normalize 去重）。"""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for x in self.mentions:
            if not isinstance(x, dict):
                continue
            if x.get("brand_id") != brand_id or x.get("status") not in {"reviewed", "active"}:
                continue
            text = str(x.get("text") or "").strip()
            key = normalize(text)
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "text": text,
                    "mention_type": str(x.get("mention_type", "alias")),
                    "match_mode": str(x.get("match_mode", "exact")),
                    "confidence": float(x.get("confidence", 1) or 0),
                    "status": str(x.get("status", "unknown")),
                }
            )
        return out

    def competitors(self, brand_id: str) -> list[dict[str, Any]]:
        """竞品关系（direct 优先、strength 降序），附目标品牌与其 mention 文本。"""
        out: list[dict[str, Any]] = []
        for rel in self.relations:
            if not isinstance(rel, dict):
                continue
            if rel.get("source_brand_id") != brand_id or rel.get("status") not in {
                "reviewed",
                "active",
            }:
                continue
            target = self._brands.get(str(rel.get("target_brand_id")))
            if not target:
                continue
            out.append(
                {
                    "relation_type": str(rel.get("relation_type", "")),
                    "strength": float(rel.get("strength", 0) or 0),
                    "brand": target,
                    "mentions": [
                        str(m.get("text"))
                        for m in self.mentions
                        if isinstance(m, dict) and m.get("brand_id") == target["brand_id"]
                    ],
                }
            )
        return sorted(out, key=lambda x: (x["relation_type"] != "direct", -float(x["strength"])))

    def compliance_context(self, brand: dict[str, Any] | None, content: str = "") -> dict[str, Any]:
        """合规提示：命中品牌 → 适用规则 + 禁用语命中；未命中 → 人工复核免责声明。"""
        if not brand:
            return {"rules": [], "hits": [], "disclaimer": "索引未命中，需人工复核"}
        wanted = set(brand.get("compliance_rule_ids", []) or [])
        cats = set(brand.get("category_ids", []) or [])
        applicable = [
            r
            for r in self.rules
            if isinstance(r, dict)
            and r.get("status") == "active"
            and (
                r.get("rule_id") in wanted
                or cats.intersection(r.get("applies_to_category_ids", []) or [])
            )
        ]
        hits: list[dict[str, Any]] = []
        for rule in applicable:
            for phrase in rule.get("prohibited_claims", []) or []:
                token = re.split(r"[/（(]", str(phrase))[0].strip()
                if token and token in content:
                    hits.append(
                        {
                            "rule_id": rule.get("rule_id"),
                            "risk_sentence": content,
                            "matched": token,
                            "risk_level": "high"
                            if rule.get("compliance_class") in {"A", "B"}
                            else "medium",
                            "suggestion": f"删除或提供权威证据并人工审核“{token}”",
                            "manual_review_required": True,
                        }
                    )
        return {
            "rules": applicable,
            "hits": hits,
            "manual_confirmation_required": any(x["risk_level"] == "high" for x in hits),
            "disclaimer": "SiliconIndex 规则仅作内容风险提示，不构成正式法律结论；"
            "缺少权威来源时需人工复核。",
        }

    def candidates(self, name: str) -> dict[str, Any]:
        """按品牌名给填表侧的索引候选：分类路径/mention 规则/竞品关系/合规提示。"""
        if not self.available:
            return {"available": False}
        brand = self.resolve(name)
        if brand is None:
            return {
                "available": True,
                "matched": False,
                "brand": None,
                "category_path": [],
                "mention_rules": [],
                "competitors": [],
                "compliance": self.compliance_context(None),
            }
        brand_id = str(brand["brand_id"])
        return {
            "available": True,
            "matched": True,
            "brand": brand,
            "category_path": self.category_path(brand),
            "mention_rules": self.mention_rules(brand_id),
            "competitors": self.competitors(brand_id),
            "compliance": self.compliance_context(brand),
        }

    def template_questions(
        self, name: str, *, region: str = "", competitor: str = ""
    ) -> dict[str, Any]:
        """模板渲染预览（candidate_only，不落库）：变量不全的模板跳过，语义去重。"""
        if not self.available:
            return {"available": False}
        brand = self.resolve(name)
        if brand is None:
            return {"available": True, "matched": False, "questions": []}
        cat_ids = set(brand.get("category_ids", []) or [])
        values = {
            "brand": str(brand.get("display_name") or brand.get("canonical_name") or ""),
            "competitor": competitor,
            "region": region,
            "category": self._category_label(brand.get("primary_category_id")),
        }
        out: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for item in self.templates:
            if not isinstance(item, dict) or item.get("category_id") not in cat_ids:
                continue
            variables = list(item.get("variables", []) or [])
            if any(not values.get(v) for v in variables):
                continue
            try:
                text = str(item["template"]).format(**values).strip()
            except (KeyError, ValueError):
                continue
            semantic = (
                item.get("intent"),
                tuple(sorted(item.get("analysis_dimensions", []) or [])),
                text,
            )
            if text and semantic not in seen:
                seen.add(semantic)
                out.append(
                    {
                        "text": text,
                        "template_id": item.get("template_id"),
                        "intent": item.get("intent"),
                        "variables": {v: values[v] for v in variables},
                        "analysis_dimensions": item.get("analysis_dimensions", []) or [],
                        "explicit_brand": "brand" in variables,
                        "index_version": self.meta.get("release_id"),
                    }
                )
        return {"available": True, "matched": True, "questions": out}
