#!/usr/bin/env python3
"""Independently recalculate Service-1 metrics from the delivered XLSX ledger."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from statistics import mean
from typing import Any
from zipfile import BadZipFile, ZipFile

_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence-package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args()


def _cell_text(cell: ET.Element) -> str:
    return "".join(node.text or "" for node in cell.iter(f"{{{_SHEET_NS}}}t"))


def _read_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            str(node.attrib["Id"]): "xl/" + str(node.attrib["Target"]).lstrip("/")
            for node in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
            if str(node.attrib.get("Type") or "").endswith("/worksheet")
        }
        output: dict[str, list[dict[str, str]]] = {}
        for sheet in workbook.findall(f".//{{{_SHEET_NS}}}sheet"):
            name = str(sheet.attrib["name"])
            relation_id = str(sheet.attrib[f"{{{_REL_NS}}}id"])
            worksheet = ET.fromstring(archive.read(targets[relation_id]))
            values = [
                [_cell_text(cell) for cell in row.findall(f"{{{_SHEET_NS}}}c")]
                for row in worksheet.findall(f".//{{{_SHEET_NS}}}row")
            ]
            if not values:
                output[name] = []
                continue
            headers = values[0]
            output[name] = [
                dict(zip(headers, [*row, *[""] * (len(headers) - len(row))], strict=False))
                for row in values[1:]
            ]
    return output


def _number(value: str) -> int | None:
    text = value.strip()
    if not text or text == "—":
        return None
    return int(float(text))


def _metric(rows: list[dict[str, str]]) -> dict[str, Any]:
    ranks = [rank for row in rows if (rank := _number(row.get("target_rank", ""))) is not None]
    total = len(rows)
    mentions = len(ranks)
    top = {str(limit): sum(rank <= limit for rank in ranks) for limit in (1, 3, 5)}
    return {
        "answers": total,
        "mentions": mentions,
        "mention_rate": round(mentions / total * 100, 1) if total else 0.0,
        "mention_rate_fraction": f"{mentions}/{total}",
        "avg_rank": round(mean(ranks), 1) if ranks else None,
        "top_counts": top,
        "top_rates": {
            key: round(value / total * 100, 1) if total else 0.0 for key, value in top.items()
        },
    }


def _metric_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "answers",
            "mentions",
            "mention_rate",
            "mention_rate_fraction",
            "avg_rank",
            "top_counts",
            "top_rates",
        )
    }


def _scoped(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, Any]]:
    values = sorted({row.get(field, "") for row in rows})
    return {value: _metric([row for row in rows if row.get(field) == value]) for value in values}


def _entity_ranking(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    entity_ranks: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        raw = row.get("canonical_entities") or "[]"
        try:
            entities = json.loads(raw)
        except json.JSONDecodeError:
            entities = []
        observed: set[str] = set()
        for entity in entities if isinstance(entities, list) else []:
            if not isinstance(entity, dict):
                continue
            canonical = str(entity.get("canonical_name") or "")
            rank = entity.get("answer_rank")
            if not canonical or canonical in observed or not isinstance(rank, int):
                continue
            observed.add(canonical)
            entity_ranks[canonical].append(rank)
    total = len(rows)
    output: list[dict[str, Any]] = [
        {
            "canonical_name": canonical,
            "answers": total,
            "mentions": len(ranks),
            "mention_rate": round(len(ranks) / total * 100, 1) if total else 0.0,
            "avg_rank": round(mean(ranks), 1),
            "top_counts": {str(limit): sum(rank <= limit for rank in ranks) for limit in (1, 3, 5)},
        }
        for canonical, ranks in entity_ranks.items()
    ]
    output.sort(key=lambda row: (-row["mentions"], row["avg_rank"], row["canonical_name"]))
    return output


def _manifest_integrity(
    *, xlsx: Path, manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    descriptor = manifest.get("artifacts", {}).get("xlsx", {})
    digest = sha256(xlsx.read_bytes()).hexdigest()
    return {
        "xlsx_sha256": digest,
        "manifest_xlsx_sha256": descriptor.get("sha256"),
        "byte_size": xlsx.stat().st_size,
        "manifest_byte_size": descriptor.get("byte_size"),
        "xlsx_matches_manifest": digest == descriptor.get("sha256")
        and xlsx.stat().st_size == descriptor.get("byte_size"),
        "manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
    }


def _stream_digest(archive: ZipFile, name: str) -> tuple[str, int]:
    digest = sha256()
    byte_size = 0
    with archive.open(name) as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _evidence_package_integrity(
    *, package_path: Path, ledger_rows: list[dict[str, str]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Verify the independently delivered evidence ZIP, including every payload byte."""

    package_descriptor = manifest.get("artifacts", {}).get("zip", {})
    package_digest = sha256(package_path.read_bytes()).hexdigest()
    failures: list[dict[str, Any]] = []
    try:
        with ZipFile(package_path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            duplicate_names = sorted(name for name, count in Counter(names).items() if count != 1)
            package_manifest = json.loads(archive.read("manifest.json"))
            descriptors = package_manifest.get("files", [])
            described_names = [str(item.get("path") or "") for item in descriptors]
            descriptor_by_name = {str(item.get("path") or ""): item for item in descriptors}
            payload_names = [name for name in names if name != "manifest.json"]
            for name in payload_names:
                descriptor = descriptor_by_name.get(name)
                if descriptor is None:
                    failures.append({"path": name, "error": "missing_descriptor"})
                    continue
                digest, byte_size = _stream_digest(archive, name)
                if digest != descriptor.get("sha256") or byte_size != descriptor.get("byte_size"):
                    failures.append(
                        {
                            "path": name,
                            "error": "digest_or_size_mismatch",
                            "calculated_sha256": digest,
                            "manifest_sha256": descriptor.get("sha256"),
                            "calculated_byte_size": byte_size,
                            "manifest_byte_size": descriptor.get("byte_size"),
                        }
                    )

            expected_sample_ids = {row.get("sample_id", "") for row in ledger_rows}
            answer_sample_ids = {
                Path(name).stem for name in payload_names if name.startswith("answers/")
            }
            metadata_sample_ids = {
                Path(name).stem for name in payload_names if name.startswith("metadata/")
            }
            names_match = (
                not duplicate_names
                and len(described_names) == len(set(described_names))
                and set(described_names) == set(payload_names)
            )
            sample_coverage = (
                answer_sample_ids == expected_sample_ids
                and metadata_sample_ids == expected_sample_ids
            )
            declared_count = package_manifest.get("file_count_excluding_manifest")
            return {
                "status": "passed"
                if (
                    not failures
                    and names_match
                    and sample_coverage
                    and declared_count == len(payload_names)
                    and package_digest == package_descriptor.get("sha256")
                    and package_path.stat().st_size == package_descriptor.get("byte_size")
                )
                else "failed",
                "package_sha256": package_digest,
                "manifest_package_sha256": package_descriptor.get("sha256"),
                "package_byte_size": package_path.stat().st_size,
                "manifest_package_byte_size": package_descriptor.get("byte_size"),
                "payload_file_count": len(payload_names),
                "declared_payload_file_count": declared_count,
                "duplicate_names": duplicate_names,
                "descriptor_names_match": names_match,
                "sample_answer_count": len(answer_sample_ids),
                "sample_metadata_count": len(metadata_sample_ids),
                "sample_coverage_matches_ledger": sample_coverage,
                "payload_failures": failures,
            }
    except (BadZipFile, KeyError, json.JSONDecodeError) as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def _is_blank_provenance(value: str) -> bool:
    return value.strip().lower() in {"", "{}", "[]", "none", "null", "unknown", "未知"}


def verify(
    *,
    xlsx: Path,
    facts_path: Path,
    manifest_path: Path,
    evidence_package: Path,
    require_ready: bool,
) -> dict[str, Any]:
    workbook = _read_workbook(xlsx)
    rows = workbook.get("样本索引", [])
    summary = {row.get("字段", ""): row.get("值", "") for row in workbook.get("说明", [])}
    facts_bundle = json.loads(facts_path.read_text(encoding="utf-8"))
    facts = facts_bundle["services"]["1"]
    delivery = facts["service1"]["delivery_v3"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    calculated = _metric(rows)
    expected = _metric_projection(delivery["target"])
    platform = _scoped(rows, "platform")
    region = _scoped(rows, "region")
    group = _scoped(rows, "group_title")
    question = _scoped(rows, "question")
    expected_questions = {
        str(row["question"]): _metric_projection(row) for row in delivery["question_rows"]
    }
    entities = _entity_ranking(rows)
    expected_entities = {
        str(row["canonical_name"]): {
            "canonical_name": row["canonical_name"],
            "answers": row["answers"],
            "mentions": row["mentions"],
            "mention_rate": row["mention_rate"],
            "avg_rank": row["avg_rank"],
            "top_counts": row["top_counts"],
        }
        for row in delivery["entity_ranking"]
    }
    observed_entities = {str(row["canonical_name"]): row for row in entities}

    cells = Counter((row.get("question"), row.get("platform"), row.get("region")) for row in rows)
    repeats = Counter(
        (
            row.get("question"),
            row.get("platform"),
            row.get("region"),
            row.get("repeat_no"),
        )
        for row in rows
    )
    frozen_questions = sorted({row.get("question", "") for row in workbook.get("问题清单", [])})
    expected_cells = [
        (question_text, platform_name, region_name)
        for question_text in frozen_questions
        for platform_name in ("doubao", "deepseek", "yiyan")
        for region_name in ("北京", "上海")
    ]
    incomplete_cells = [
        {
            "question": question_text,
            "platform": platform_name,
            "region": region_name,
            "observed_sample_count": cells[(question_text, platform_name, region_name)],
            "observed_repeat_numbers": sorted(
                row.get("repeat_no", "")
                for row in rows
                if (
                    row.get("question") == question_text
                    and row.get("platform") == platform_name
                    and row.get("region") == region_name
                )
            ),
            "observed_run_ids": sorted(
                row.get("run_id", "")
                for row in rows
                if (
                    row.get("question") == question_text
                    and row.get("platform") == platform_name
                    and row.get("region") == region_name
                )
            ),
        }
        for question_text, platform_name, region_name in expected_cells
        if cells[(question_text, platform_name, region_name)] != 2
        or {
            row.get("repeat_no", "")
            for row in rows
            if (
                row.get("question") == question_text
                and row.get("platform") == platform_name
                and row.get("region") == region_name
            )
        }
        != {"1", "2"}
    ]
    provenance_fields = (
        "account_id_masked",
        "browser_instance",
        "egress_audit",
        "egress_region_gb",
        "run_id",
    )
    provenance_missing_counts = {
        field: sum(_is_blank_provenance(row.get(field, "")) for row in rows)
        for field in provenance_fields
    }
    evidence_integrity = _evidence_package_integrity(
        package_path=evidence_package, ledger_rows=rows, manifest=manifest
    )
    matrix_ready = (
        len(rows) == 144
        and len(cells) == 72
        and set(cells.values()) == {2}
        and set(repeats.values()) == {1}
        and {row.get("platform") for row in rows} == {"doubao", "deepseek", "yiyan"}
        and {row.get("region") for row in rows} == {"北京", "上海"}
    )
    checks = {
        "target_brand_matches": summary.get("目标品牌") == facts.get("target_brand"),
        "target_metric_matches": calculated == expected,
        "platform_metrics_match": {
            key: _metric_projection(value) for key, value in platform.items()
        }
        == {key: _metric_projection(value) for key, value in delivery["by_platform"].items()},
        "region_metrics_match": {key: _metric_projection(value) for key, value in region.items()}
        == {key: _metric_projection(value) for key, value in delivery["by_region"].items()},
        "group_metrics_match": {key: _metric_projection(value) for key, value in group.items()}
        == {key: _metric_projection(value) for key, value in delivery["by_group"].items()},
        "question_metrics_match": {
            key: _metric_projection(value) for key, value in question.items()
        }
        == expected_questions,
        "entity_metrics_match": observed_entities == expected_entities,
        "citation_counts_match": sum(int(row.get("citation_count") or 0) for row in rows)
        == int(delivery["scope"]["citation_references"]),
        "xlsx_manifest_integrity": _manifest_integrity(
            xlsx=xlsx, manifest_path=manifest_path, manifest=manifest
        )["xlsx_matches_manifest"],
        "evidence_package_integrity": evidence_integrity.get("status") == "passed",
    }
    metrics_pass = all(checks.values())
    gate_ready = manifest.get("data_gate", {}).get("status") == "ready"
    overall_pass = metrics_pass and (not require_ready or matrix_ready and gate_ready)
    return {
        "schema_version": "service1-independent-recalculation-v1",
        "status": "passed" if overall_pass else "failed",
        "metric_recalculation_status": "passed" if metrics_pass else "failed",
        "delivery_readiness_status": "ready" if matrix_ready and gate_ready else "blocked",
        "checks": checks,
        "matrix": {
            "observed_samples": len(rows),
            "expected_samples": 144,
            "observed_cells": len(cells),
            "expected_cells": 72,
            "cell_repeat_counts": dict(Counter(cells.values())),
            "incomplete_cells": incomplete_cells,
            "missing_provenance_counts": provenance_missing_counts,
            "independent_repeat_false_count": sum(
                row.get("independent_repeat", "").strip().lower() != "true" for row in rows
            ),
            "ready": matrix_ready,
        },
        "calculated_target": calculated,
        "expected_target": expected,
        "integrity": _manifest_integrity(xlsx=xlsx, manifest_path=manifest_path, manifest=manifest),
        "evidence_package_integrity": evidence_integrity,
        "manifest_data_gate": manifest.get("data_gate"),
    }


def main() -> int:
    args = _arguments()
    result = verify(
        xlsx=args.xlsx,
        facts_path=args.facts,
        manifest_path=args.manifest,
        evidence_package=args.evidence_package,
        require_ready=args.require_ready,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(args.output.resolve())
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
