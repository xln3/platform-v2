"""SiliconIndex source/sink adapter outside the domain-neutral core."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from domain.knowledge_evolution.connectors import ConnectorResult
from domain.knowledge_evolution.merge import MergeConflict, MergeResult, three_way_merge

from .snapshot import FILES, SiliconIndexSyncError, validate_snapshot

_ID_FIELDS = {
    "brands": "brand_id",
    "mentions": "mention_id",
    "categories": "category_id",
    "cognition-profiles": "profile_id",
    "compliance-rules": "rule_id",
    "competitor-relations": "relation_id",
    "query-templates": "template_id",
}
_FORBIDDEN_PUBLIC_KEYS = {
    "answer_text",
    "context",
    "customer",
    "customer_name",
    "project",
    "project_name",
    "prompt",
    "safe_context",
    "tenant",
    "tenant_pub_id",
}
_RELATIONSHIP_BY_MENTION_TYPE = {
    "canonical": "self",
    "official_name": "same_legal_entity",
    "company_name": "same_legal_entity",
    "common_alias": "trade_name",
    "english": "english_name",
    "abbreviation": "official_abbreviation",
    "product_line": "product_of",
    "historical_name": "historical_name",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_dir(value: str | Path) -> Path:
    root = Path(value).resolve()
    pointer = root / "CURRENT"
    if pointer.is_file():
        root = root / pointer.read_text(encoding="utf-8").strip()
    for candidate in (root, root / "data" / "v1", root / "public" / "data" / "v1"):
        if (candidate / "manifest.json").is_file():
            return candidate
    raise SiliconIndexSyncError("snapshot_data_dir_missing")


def _key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def project_brand_domain(source: str | Path, *, analysis_domain: str) -> dict[str, Any]:
    """Compile a validated static release into the governed brand read model."""

    data_dir = _data_dir(source)
    manifest = validate_snapshot(data_dir)
    brands = _read(data_dir / "brands.json")
    mentions = _read(data_dir / "mentions.json")
    by_brand: dict[str, list[dict[str, Any]]] = {}
    for mention in mentions:
        if isinstance(mention, dict):
            by_brand.setdefault(str(mention.get("brand_id") or ""), []).append(mention)
    alias_owners: dict[str, str] = {}
    entities: list[dict[str, Any]] = []
    for brand in brands:
        if not isinstance(brand, dict) or brand.get("status") != "active":
            continue
        profiles = [
            item
            for item in brand.get("comparison_profiles", [])
            if isinstance(item, dict) and item.get("domain") == analysis_domain
        ]
        if not profiles:
            continue
        if len(profiles) != 1:
            raise SiliconIndexSyncError(f"duplicate_comparison_profile:{brand.get('brand_id')}")
        profile = profiles[0]
        brand_id = str(brand.get("brand_id") or "").strip()
        canonical = str(brand.get("canonical_name") or "").strip()
        if not brand_id or not canonical:
            raise SiliconIndexSyncError("brand_identity_missing")
        review_status = str(brand.get("review_status") or "pending")
        aliases: list[str] = []
        relationships: dict[str, str] = {}
        for mention in sorted(
            by_brand.get(brand_id, []), key=lambda row: str(row.get("mention_id") or "")
        ):
            mention_status = str(mention.get("status") or "pending")
            if review_status == "reviewed" and mention_status != "reviewed":
                continue
            if mention_status not in {"reviewed", "pending"}:
                continue
            if mention.get("match_mode") not in {"exact", "normalized_exact"}:
                continue
            text = str(mention.get("text") or "").strip()
            if not text or text == canonical:
                continue
            aliases.append(text)
            relationships[text] = str(
                mention.get("relationship_to_brand")
                or _RELATIONSHIP_BY_MENTION_TYPE.get(
                    str(mention.get("mention_type") or ""),
                    "official_abbreviation",
                )
            )
        for alias in (canonical, *aliases):
            owner = alias_owners.get(_key(alias))
            if owner is not None and owner != brand_id:
                raise SiliconIndexSyncError(
                    f"cross_brand_alias_conflict:{alias}:{owner}:{brand_id}"
                )
            alias_owners[_key(alias)] = brand_id
        reviewed = review_status == "reviewed"
        entities.append(
            {
                "entity_id": brand_id,
                "canonical_name": canonical,
                "aliases": list(dict.fromkeys(aliases)),
                "alias_relationships": relationships,
                "entity_type": str(brand.get("entity_type") or "company"),
                "competitor_eligible": bool(profile.get("competitor_eligible")) and reviewed,
                "eligibility_mode": str(profile.get("eligibility_mode") or "always"),
                "brand_level": str(brand.get("brand_level") or "brand"),
                "parent_brand": brand.get("parent_brand_name"),
                "industry_fit": profile.get("industry_fit"),
                "competitor_scopes": list(profile.get("competitor_scopes") or []),
                "eligibility_note": profile.get("eligibility_note")
                or ("SiliconIndex 实体仍在待审状态，不进入正式竞品榜。" if not reviewed else None),
                "evidence_urls": list(profile.get("evidence_urls") or []),
                "review_status": review_status,
            }
        )
    entities.sort(key=lambda row: str(row["entity_id"]))
    return {
        "schema_version": "entity-master-v3",
        "domain": analysis_domain,
        "revision": f"siliconindex:{manifest['release_id']}:{analysis_domain}",
        "aggregation_level": "brand_family",
        "source_system": "siliconindex",
        "source_release_id": manifest["release_id"],
        "source_content_hash": manifest["content_hash"],
        "source_url": "https://siliconindex-consumer.onrender.com/data/v1",
        "resolution_policy": (
            "实体 ID、别名关系、证据和审核状态来自同一 SiliconIndex 发布快照；"
            "正式榜单仅消费 reviewed 实体。项目观测只能产生待审候选，不能在项目内"
            "创建实体真相；品牌家族归并不等于同一法人。此文件是生成的离线投影，"
            "不是第二事实源。"
        ),
        "entities": entities,
    }


def _dataset_map(name: str, value: Any) -> Any:
    id_field = _ID_FIELDS.get(name)
    if id_field is None or not isinstance(value, list):
        return value
    output: dict[str, Any] = {}
    for row in value:
        if not isinstance(row, dict) or not row.get(id_field):
            raise SiliconIndexSyncError(f"merge_identity_missing:{name}")
        row_id = str(row[id_field])
        if row_id in output:
            raise SiliconIndexSyncError(f"merge_identity_duplicate:{name}:{row_id}")
        output[row_id] = row
    return output


def _dataset_list(name: str, value: Any) -> Any:
    if name not in _ID_FIELDS or not isinstance(value, dict):
        return value
    return [value[key] for key in sorted(value)]


class SiliconIndexAdapter:
    adapter_id = "siliconindex-static"
    adapter_version = "1"

    def import_release(self, source: str) -> ConnectorResult:
        data_dir = _data_dir(source)
        manifest = validate_snapshot(data_dir)
        return ConnectorResult(
            adapter=self.adapter_id,
            operation="import",
            status="success",
            upstream_release_id=str(manifest["release_id"]),
            result={
                "content_hash": manifest["content_hash"],
                "schema_version": manifest.get("schema_version"),
                "source": str(data_dir),
            },
        )

    def export_changes(self, changes: tuple[Mapping[str, Any], ...]) -> ConnectorResult:
        exported: list[dict[str, Any]] = []
        for change in changes:
            if change.get("visibility") != "public":
                continue
            if change.get("review_status") not in {"reviewed", "published"}:
                continue
            rendered = json.dumps(change, ensure_ascii=False).casefold()
            if any(f'"{key}"' in rendered for key in _FORBIDDEN_PUBLIC_KEYS):
                raise SiliconIndexSyncError("public_export_contains_private_field")
            attributes = change.get("attributes")
            if not isinstance(attributes, dict) or not attributes.get("evidence_urls"):
                raise SiliconIndexSyncError("public_export_evidence_required")
            exported.append(dict(change))
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    exported, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        return ConnectorResult(
            adapter=self.adapter_id,
            operation="export",
            status="success",
            result={"changes": exported, "count": len(exported), "content_hash": digest},
        )

    def reconcile(self, base: Any, upstream: Any, local: Any) -> MergeResult:
        if not all(isinstance(value, dict) for value in (base, upstream, local)):
            return three_way_merge(base, upstream, local)
        base_map = {name: _dataset_map(name, base.get(name)) for name in FILES}
        upstream_map = {name: _dataset_map(name, upstream.get(name)) for name in FILES}
        local_map = {name: _dataset_map(name, local.get(name)) for name in FILES}
        result = three_way_merge(base_map, upstream_map, local_map)
        merged = {
            name: _dataset_list(name, result.merged[name])
            for name in FILES
            if name in result.merged
        }
        conflicts = tuple(
            MergeConflict(
                path=f"/datasets{conflict.path}",
                base=conflict.base,
                upstream=conflict.upstream,
                local=conflict.local,
            )
            for conflict in result.conflicts
        )
        return MergeResult(merged=merged, conflicts=conflicts)


def load_datasets(source: str | Path) -> dict[str, Any]:
    data_dir = _data_dir(source)
    validate_snapshot(data_dir)
    return {name: _read(data_dir / f"{name}.json") for name in FILES}


__all__ = ["SiliconIndexAdapter", "load_datasets", "project_brand_domain"]
