from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any

import pytest
from docx import Document
from geo_platform.reports import formal_review_service2_source_corpus as builder

from domain.reporting.formal_review_service2_source_corpus_docx import (
    render_service2_source_corpus_docx,
)


def _manifest_facts() -> dict[str, Any]:
    return {
        "schema_version": "formal-service2-source-corpus-v2",
        "scope": {
            "project_pub_id": "prj_service2",
            "batch_pub_id": "s2b_service2",
            "run_pub_ids": ["run_a", "run_b"],
        },
        "coverage": {
            "expected_occurrences": 5,
            "materialized_items": 5,
            "distinct_urls": 3,
            "processing_states": {"processed": 4, "blocked": 1},
            "fetch_states": {"succeeded": 4, "blocked": 1},
            "entered_judgment": 4,
            "findings": 3,
            "reviewed_findings": 3,
            "eligible_cases": 2,
            "coverage_complete": True,
        },
        "cases": [
            {
                "finding_pub_id": "s2f_case",
                "level": "L2b",
                "is_disparagement": True,
                "relation_direction": "target_degraded_peer_elevated",
                "textual_speaker": "页面叙述者",
                "target_entity": "目标品牌",
                "evidence_quote": "目标品牌被无锚点地置于次级位置。",
                "canonical_url": "https://example.com/case",
                "snapshot_pub_id": "snp_case",
                "snapshot_text_sha256": "a" * 64,
                "factcheck_verdict": "unverifiable",
                "factcheck_evidence": [],
                "factcheck_boundary": "当前公开材料不足，不能判断该陈述真假。",
                "publisher_attribution": {
                    "party": None,
                    "confidence": "unknown",
                    "evidence": [],
                },
                "commissioner_attribution": {
                    "party": None,
                    "confidence": "unknown",
                    "evidence": [],
                },
            }
        ],
        "evidence_pub_ids": ["evd_case"],
        "rendering_boundary": "frozen_facts_only_no_network_or_model",
    }


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _Connection:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[str] = []

    def execute(self, statement: str, _params: object) -> _Result:
        self.statements.append(statement)
        return _Result(self.row)


def _document_text(payload: bytes) -> str:
    document = Document(BytesIO(payload))
    values = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        values.extend(cell.text for row in table.rows for cell in row.cells)
    return "\n".join(values)


def test_builder_reads_one_hash_verified_manifest_and_discloses_blocked_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _manifest_facts()
    row = {
        "manifest_pub_id": "s2m_service2",
        "revision": 1,
        "manifest_hash": _canonical_hash(facts),
        "facts": facts,
        "case_count": 1,
        "evidence_reference_count": 1,
        "created_at": datetime(2026, 8, 24, tzinfo=UTC),
        "batch_pub_id": "s2b_service2",
        "corpus_policy_version": "service2-all-u-occurrence-v1",
        "judgment_policy_version": "service2-entity-relation-v1",
        "project_name": "全 U 核查项目",
        "target_brand": "目标品牌",
    }
    connection = _Connection(row)

    @contextmanager
    def fake_connection(*_args: object, **_kwargs: object) -> Any:
        yield connection

    monkeypatch.setattr(builder, "tenant_connection", fake_connection)
    result = builder.build_service2_source_corpus_facts(
        dsn="postgresql://unused",
        tenant_pub_id="tnt_service2",
        project_pub_id="prj_service2",
        start=date(2026, 8, 1),
        end=date(2026, 8, 24),
        generated_at=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert result["coverage"]["expected_occurrences"] == 5
    assert result["coverage"]["distinct_urls"] == 3
    assert result["evidence_gate"] == {
        "status": "insufficient",
        "reasons": ["source_or_evidence_coverage_incomplete"],
        "incomplete_processing_states": {"blocked": 1},
    }
    assert result["rendering_boundary"] == "frozen_facts_only_no_network_or_model"
    assert "service2_fact_manifest" in connection.statements[0]
    assert "AT TIME ZONE 'Asia/Shanghai'" in connection.statements[0]


def test_builder_rejects_manifest_hash_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = _manifest_facts()
    connection = _Connection(
        {
            "manifest_pub_id": "s2m_service2",
            "revision": 1,
            "manifest_hash": "0" * 64,
            "facts": facts,
            "case_count": 1,
            "evidence_reference_count": 1,
            "created_at": datetime(2026, 8, 24, tzinfo=UTC),
            "batch_pub_id": "s2b_service2",
            "corpus_policy_version": "service2-all-u-occurrence-v1",
            "judgment_policy_version": "service2-entity-relation-v1",
            "project_name": "全 U 核查项目",
            "target_brand": "目标品牌",
        }
    )

    @contextmanager
    def fake_connection(*_args: object, **_kwargs: object) -> Any:
        yield connection

    monkeypatch.setattr(builder, "tenant_connection", fake_connection)
    with pytest.raises(ValueError, match="service2_frozen_manifest_integrity_failed"):
        builder.build_service2_source_corpus_facts(
            dsn="postgresql://unused",
            tenant_pub_id="tnt_service2",
            project_pub_id="prj_service2",
            start=date(2026, 8, 1),
            end=date(2026, 8, 24),
            generated_at=datetime(2026, 8, 24, tzinfo=UTC),
        )


def test_docx_keeps_occurrence_and_url_counts_separate_and_unknown_attribution() -> None:
    facts = {
        **_manifest_facts(),
        "project_name": "全 U 核查项目",
        "target_brand": "目标品牌",
        "generated_at": datetime(2026, 8, 24, tzinfo=UTC),
        "window": {"start": "2026-08-01", "end": "2026-08-24"},
        "document_status": "internal_review",
        "document_governance": {
            "version": "V1.0",
            "prepared_by": "GEO 项目组",
            "prepared_date": "2026-08-24",
        },
        "manifest": {
            "batch_pub_id": "s2b_service2",
            "manifest_pub_id": "s2m_service2",
            "revision": 1,
            "manifest_hash": "b" * 64,
            "corpus_policy_version": "service2-all-u-occurrence-v1",
            "judgment_policy_version": "service2-entity-relation-v1",
        },
        "evidence_gate": {
            "status": "insufficient",
            "reasons": ["source_or_evidence_coverage_incomplete"],
            "incomplete_processing_states": {"blocked": 1},
        },
        "limitations": [
            "入池总体为全部 U occurrence；URL 抓取复用不缩小分母。",
            "publisher/commissioner 归属为 unknown 时不作委托归因。",
        ],
    }

    text = _document_text(render_service2_source_corpus_docx(facts))

    assert "主动拉踩内容核查报告" in text
    assert "全部 U 信源帖子实体—关系核查" in text
    assert "U occurrence\n5\n冻结范围总体" in text
    assert "distinct URL\n3\n仅用于抓取复用" in text
    assert "unknown（未作归因）" in text
    assert "当前公开材料不足，不能判断该陈述真假。" in text
    assert "service2-all-u-occurrence-v1" in text
    assert "b" * 64 in text
    assert "本稿不得被表述为完整无风险结论" in text
