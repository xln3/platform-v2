"""Customer-auditable sidecars for the governed Service-1 report.

The DOCX deliberately stays concise.  This module carries the complete sample
ledger and byte-for-byte evidence into an XLSX index and a deterministic ZIP so
every number in the report can be traced back to one observation.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

from .artifacts import render_xlsx_workbook

BlobLoader = Callable[[str, str], bytes]


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _json_bytes(value: object, *, pretty: bool = True) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        default=_json_default,
    ).encode()


def _cell_json(value: object) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _cst(value: object) -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S 中国标准时间（UTC+8）"
    )


def _safe_name(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "asset")).strip("._")
    return text[:80] or "asset"


def _extension(mime_type: object) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "text/html": ".html",
        "text/plain": ".txt",
        "application/json": ".json",
        "application/pdf": ".pdf",
    }.get(str(mime_type).split(";", 1)[0].lower(), ".bin")


def _descriptor_paths(sample: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    images: list[str] = []
    shares: list[str] = []
    sample_id = str(sample.get("sample_id") or "sample")
    for ordinal, descriptor in enumerate(sample.get("all_evidence") or [], 1):
        if not isinstance(descriptor, Mapping):
            continue
        name = (
            f"evidence/{sample_id}/{ordinal:02d}_"
            f"{_safe_name(descriptor.get('kind'))}_{_safe_name(descriptor.get('evidence_id'))}"
            f"{_extension(descriptor.get('mime_type'))}"
        )
        if descriptor.get("kind") == "share_image":
            shares.append(name)
        elif descriptor.get("kind") in {"answer_screenshot", "answer_excerpt_screenshot"}:
            images.append(name)
    return images, shares


def _sample_rows(registry: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in registry:
        screenshot_paths, share_paths = _descriptor_paths(sample)
        rows.append(
            {
                "sample_id": sample.get("sample_id"),
                "repeat_no": sample.get("repeat_no"),
                "capture_time_CST": _cst(sample.get("capture_time")),
                "platform": sample.get("platform"),
                "mode": sample.get("mode"),
                "region": sample.get("region"),
                "account_id_masked": sample.get("account_id_masked"),
                "browser_instance": sample.get("browser_instance"),
                "egress_region_gb": sample.get("egress_region_gb"),
                "egress_audit": _cell_json(sample.get("egress_audit")),
                "run_id": sample.get("run_id"),
                "independent_repeat": sample.get("independent_repeat"),
                "group_title": sample.get("group_title"),
                "question": sample.get("question"),
                "target_mentioned": sample.get("mentioned"),
                "target_rank": sample.get("target_rank"),
                "canonical_entities": _cell_json(sample.get("entities")),
                "citation_count": sample.get("citation_count"),
                "answer_path": (sample.get("answer_evidence") or {}).get("path"),
                "answer_sha256": (sample.get("answer_evidence") or {}).get("sha256"),
                "answer_byte_size": (sample.get("answer_evidence") or {}).get("byte_size"),
                "screenshot_paths": "\n".join(screenshot_paths),
                "share_image_paths": "\n".join(share_paths),
                "citation_index_path": f"citations/{sample.get('sample_id')}.json",
                "sample_metadata_path": f"metadata/{sample.get('sample_id')}.json",
                "response_text": sample.get("response_text"),
            }
        )
    return rows


def _citation_rows(registry: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in registry:
        snapshots = {
            str(item.get("url") or ""): item
            for item in sample.get("citation_snapshots") or []
            if isinstance(item, Mapping)
        }
        for citation in sample.get("citations") or []:
            if not isinstance(citation, Mapping):
                continue
            url = str(citation.get("url") or "")
            snapshot = snapshots.get(url, {})
            image = snapshot.get("snapshot") if isinstance(snapshot, Mapping) else None
            rows.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "ordinal": citation.get("ordinal"),
                    "host": citation.get("host"),
                    "title": citation.get("title"),
                    "url": url,
                    "visited_at_CST": _cst(snapshot.get("visited_at")),
                    "http_status": snapshot.get("http_status"),
                    "extract_status": snapshot.get("extract_status"),
                    "final_url": snapshot.get("final_url"),
                    "text_sha256": snapshot.get("text_sha256"),
                    "snapshot_sha256": image.get("sha256") if isinstance(image, Mapping) else "",
                }
            )
    return rows


def _evidence_rows(registry: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in registry:
        sample_id = str(sample.get("sample_id") or "")
        for ordinal, descriptor in enumerate(sample.get("all_evidence") or [], 1):
            if not isinstance(descriptor, Mapping):
                continue
            path = (
                f"evidence/{sample_id}/{ordinal:02d}_"
                f"{_safe_name(descriptor.get('kind'))}_{_safe_name(descriptor.get('evidence_id'))}"
                f"{_extension(descriptor.get('mime_type'))}"
            )
            rows.append(
                {
                    "sample_id": sample_id,
                    "evidence_id": descriptor.get("evidence_id"),
                    "relation_type": descriptor.get("relation_type"),
                    "kind": descriptor.get("kind"),
                    "capture_time_CST": _cst(descriptor.get("capture_time")),
                    "mime_type": descriptor.get("mime_type"),
                    "byte_size": descriptor.get("byte_size"),
                    "sha256": descriptor.get("sha256"),
                    "package_path": path,
                    "source_url": descriptor.get("source_url"),
                }
            )
        for ordinal, snapshot in enumerate(sample.get("citation_snapshots") or [], 1):
            if not isinstance(snapshot, Mapping):
                continue
            text_key = snapshot.get("text_object_key")
            text_sha = snapshot.get("text_sha256")
            if text_key and text_sha:
                rows.append(
                    {
                        "sample_id": sample_id,
                        "evidence_id": snapshot.get("document_id"),
                        "relation_type": "cited_source_document",
                        "kind": "webpage_text_snapshot",
                        "capture_time_CST": _cst(snapshot.get("visited_at")),
                        "mime_type": "text/plain; charset=utf-8",
                        "byte_size": snapshot.get("byte_size"),
                        "sha256": text_sha,
                        "package_path": f"web_snapshots/{sample_id}/{ordinal:03d}_text.txt",
                        "source_url": snapshot.get("url"),
                    }
                )
            image = snapshot.get("snapshot")
            if isinstance(image, Mapping):
                rows.append(
                    {
                        "sample_id": sample_id,
                        "evidence_id": image.get("evidence_id"),
                        "relation_type": "brand_mention_source_snapshot",
                        "kind": "webpage_visual_snapshot",
                        "capture_time_CST": _cst(snapshot.get("visited_at")),
                        "mime_type": image.get("mime_type"),
                        "byte_size": image.get("byte_size"),
                        "sha256": image.get("sha256"),
                        "package_path": (
                            f"web_snapshots/{sample_id}/{ordinal:03d}_visual"
                            f"{_extension(image.get('mime_type'))}"
                        ),
                        "source_url": snapshot.get("url"),
                    }
                )
    return rows


def render_service1_index_xlsx(facts: Mapping[str, Any]) -> bytes:
    delivery = facts["service1"]["delivery_v3"]
    registry = list(delivery.get("sample_registry") or [])
    registration = facts["service1"].get("scope_registration") or {}
    governance = facts.get("document_governance") or {}
    summary = [
        {"字段": "目标品牌", "值": facts.get("target_brand"), "说明": "规范实体名"},
        {
            "字段": "发布状态",
            "值": facts.get("document_status"),
            "说明": "状态应与报告、manifest和审批记录一致",
        },
        {"字段": "版本", "值": governance.get("version") or "V1.0", "说明": ""},
        {"字段": "评测范围", "值": delivery["scope"].get("scope_label"), "说明": ""},
        {"字段": "实际样本", "值": len(registry), "说明": "每行对应一次观测"},
        {"字段": "目标样本", "值": 144, "说明": "3组×4问×3平台×2地域×2重复"},
        {
            "字段": "选题登记",
            "值": registration.get("status"),
            "说明": ";".join(registration.get("reasons") or []),
        },
        {
            "字段": "报价门禁",
            "值": delivery["quotation_gate"].get("status"),
            "说明": ";".join(delivery["quotation_gate"].get("reasons") or []),
        },
        {"字段": "时区", "值": "中国标准时间（UTC+8）", "说明": "客户可见时间统一口径"},
    ]
    questions = [
        {
            "group_index": group.get("index"),
            "group_title": group.get("title"),
            "service_number": group.get("service_number"),
            "quotation_appendix": group.get("quotation_appendix"),
            "question_group_hash": group.get("question_group_hash"),
            "question_index": ordinal,
            "variant": "原题" if ordinal == 1 else chr(63 + ordinal),
            "question": question,
        }
        for group in delivery.get("selected_groups") or []
        for ordinal, question in enumerate(group.get("questions") or [], 1)
    ]
    comparison = [delivery["competitor_comparison"]["target"]]
    comparison.extend(delivery["competitor_comparison"].get("competitors") or [])
    return bytes(
        render_xlsx_workbook(
            {
                "说明": summary,
                "样本索引": _sample_rows(registry),
                "问题冻结": questions,
                "实体排名": list(delivery.get("entity_ranking") or []),
                "竞品对比": comparison,
                "同题同平台": list(
                    delivery["competitor_comparison"].get("same_question_platform") or []
                ),
                "引用URL": _citation_rows(registry),
                "证据文件": _evidence_rows(registry),
                "重复一致性": list(delivery["repeat_consistency"].get("details") or []),
            }
        )
    )


def _zip_write(archive: ZipFile, path: str, payload: bytes) -> None:
    normalized = str(PurePosixPath(path))
    if normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError("unsafe_evidence_package_path")
    info = ZipInfo(normalized, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def render_service1_evidence_package(facts: Mapping[str, Any], *, blob_loader: BlobLoader) -> bytes:
    delivery = facts["service1"]["delivery_v3"]
    registry = list(delivery.get("sample_registry") or [])
    file_manifest: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}

    def add(path: str, payload: bytes, *, sample_id: str | None, kind: str) -> None:
        existing = payloads.get(path)
        if existing is not None and existing != payload:
            raise ValueError("evidence_package_path_collision")
        payloads[path] = payload
        file_manifest.append(
            {
                "path": path,
                "sample_id": sample_id,
                "kind": kind,
                "byte_size": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
        )

    for sample in registry:
        sample_id = str(sample.get("sample_id") or "")
        response = str(sample.get("response_text") or "").encode()
        expected = sample.get("answer_evidence") or {}
        if sha256(response).hexdigest() != expected.get("sha256"):
            raise ValueError(f"answer_digest_drift:{sample_id}")
        add(str(expected.get("path")), response, sample_id=sample_id, kind="complete_answer")
        citations = _json_bytes(
            [
                {key: value for key, value in citation.items() if key != "cited_text"}
                for citation in sample.get("citations") or []
                if isinstance(citation, Mapping)
            ]
        )
        add(f"citations/{sample_id}.json", citations, sample_id=sample_id, kind="citation_index")

        public_metadata = {
            key: value
            for key, value in sample.items()
            if key
            not in {
                "response_text",
                "all_evidence",
                "screenshot_evidence",
                "share_image_evidence",
            }
        }
        add(
            f"metadata/{sample_id}.json",
            _json_bytes(public_metadata),
            sample_id=sample_id,
            kind="sample_metadata",
        )
        for ordinal, descriptor in enumerate(sample.get("all_evidence") or [], 1):
            if not isinstance(descriptor, Mapping):
                continue
            payload = blob_loader(str(descriptor["object_key"]), str(descriptor["sha256"]))
            path = (
                f"evidence/{sample_id}/{ordinal:02d}_{_safe_name(descriptor.get('kind'))}_"
                f"{_safe_name(descriptor.get('evidence_id'))}{_extension(descriptor.get('mime_type'))}"
            )
            add(path, payload, sample_id=sample_id, kind=str(descriptor.get("kind") or "evidence"))
        for ordinal, snapshot in enumerate(sample.get("citation_snapshots") or [], 1):
            if not isinstance(snapshot, Mapping):
                continue
            if snapshot.get("text_object_key") and snapshot.get("text_sha256"):
                payload = blob_loader(
                    str(snapshot["text_object_key"]), str(snapshot["text_sha256"])
                )
                add(
                    f"web_snapshots/{sample_id}/{ordinal:03d}_text.txt",
                    payload,
                    sample_id=sample_id,
                    kind="webpage_text_snapshot",
                )
            image = snapshot.get("snapshot")
            if isinstance(image, Mapping):
                payload = blob_loader(str(image["object_key"]), str(image["sha256"]))
                add(
                    f"web_snapshots/{sample_id}/{ordinal:03d}_visual"
                    f"{_extension(image.get('mime_type'))}",
                    payload,
                    sample_id=sample_id,
                    kind="webpage_visual_snapshot",
                )

    csv_buffer = StringIO(newline="")
    csv_rows = _evidence_rows(registry)
    csv_columns = list(csv_rows[0]) if csv_rows else ["sample_id", "package_path", "sha256"]
    writer = csv.DictWriter(csv_buffer, fieldnames=csv_columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(csv_rows)
    add("证据文件索引.csv", csv_buffer.getvalue().encode("utf-8-sig"), sample_id=None, kind="index")

    package_manifest = {
        "schema_version": "service1-evidence-package-v1",
        "document_status": facts.get("document_status"),
        "timezone": "Asia/Shanghai (UTC+8)",
        "sample_count": len(registry),
        "file_count_excluding_manifest": len(file_manifest),
        "files": sorted(file_manifest, key=lambda row: str(row["path"])),
    }
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for path in sorted(payloads):
            _zip_write(archive, path, payloads[path])
        _zip_write(archive, "manifest.json", _json_bytes(package_manifest))
    return output.getvalue()


def render_service1_sidecars(
    facts: Mapping[str, Any], *, blob_loader: BlobLoader
) -> dict[str, bytes]:
    return {
        "xlsx": render_service1_index_xlsx(facts),
        "zip": render_service1_evidence_package(facts, blob_loader=blob_loader),
    }


__all__ = [
    "render_service1_evidence_package",
    "render_service1_index_xlsx",
    "render_service1_sidecars",
]
