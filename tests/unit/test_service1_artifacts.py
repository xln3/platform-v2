from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from domain.reporting.service1_artifacts import render_service1_sidecars


def _facts() -> tuple[dict, bytes]:
    image = b"unit-image-payload"
    sample = {
        "sample_id": "S1-0001",
        "repeat_no": 1,
        "capture_time": "2026-08-14T01:00:00+00:00",
        "platform": "doubao",
        "mode": "browser",
        "region": "北京",
        "account_id_masked": "acct-7a91",
        "browser_instance": "doubao_beijing_01",
        "egress_region_gb": "北京",
        "egress_audit": {"ip_sha256": "a" * 64, "probe_state": "ok"},
        "run_id": "run-one",
        "independent_repeat": True,
        "group_title": "业务场景一",
        "question": "问题一",
        "mentioned": True,
        "target_rank": 2,
        "entities": [
            {
                "canonical_name": "目标品牌",
                "answer_rank": 2,
                "entity_type": "company",
                "competitor_eligible": True,
            }
        ],
        "citation_count": 1,
        "citations": [{"ordinal": 1, "host": "example.com", "url": "https://example.com"}],
        "citation_snapshots": [],
        "response_text": "完整回答",
        "answer_evidence": {
            "path": "answers/S1-0001.txt",
            "sha256": sha256("完整回答".encode()).hexdigest(),
            "byte_size": len("完整回答".encode()),
        },
        "all_evidence": [
            {
                "evidence_id": "evd-one",
                "relation_type": "answer_evidence",
                "kind": "answer_screenshot",
                "capture_time": "2026-08-14T01:00:00+00:00",
                "mime_type": "image/png",
                "byte_size": len(image),
                "sha256": sha256(image).hexdigest(),
                "object_key": "sha256/unit-image",
                "source_url": None,
            }
        ],
    }
    metric = {
        "canonical_name": "目标品牌",
        "answers": 1,
        "mentions": 1,
        "mention_rate": 100.0,
        "mention_rate_fraction": "1/1",
        "avg_rank": 2.0,
        "top_counts": {"1": 0, "3": 1, "5": 1},
        "top_rates": {"1": 0.0, "3": 100.0, "5": 100.0},
    }
    facts = {
        "target_brand": "目标品牌",
        "document_status": "internal_review",
        "document_governance": {"version": "V1.0"},
        "service1": {
            "scope_registration": {"status": "registered", "reasons": []},
            "delivery_v3": {
                "scope": {"scope_label": "三个业务场景"},
                "quotation_gate": {"status": "ready", "reasons": []},
                "selected_groups": [],
                "entity_ranking": [],
                "competitor_comparison": {
                    "target": metric,
                    "competitors": [],
                    "same_question_platform": [],
                },
                "repeat_consistency": {"details": []},
                "sample_registry": [sample],
            },
        },
    }
    return facts, image


def test_service1_sidecars_locate_complete_answer_and_hashed_visual() -> None:
    facts, image = _facts()
    sidecars = render_service1_sidecars(
        facts,
        blob_loader=lambda key, digest: (
            image if key == "sha256/unit-image" and digest == sha256(image).hexdigest() else b""
        ),
    )

    with ZipFile(BytesIO(sidecars["xlsx"])) as workbook:
        workbook_xml = workbook.read("xl/workbook.xml").decode()
        assert "样本索引" in workbook_xml
        assert "证据文件" in workbook_xml
        assert len([name for name in workbook.namelist() if "worksheets/sheet" in name]) == 9

    with ZipFile(BytesIO(sidecars["zip"])) as package:
        names = package.namelist()
        assert "answers/S1-0001.txt" in names
        assert package.read("answers/S1-0001.txt").decode() == "完整回答"
        evidence_path = next(name for name in names if name.startswith("evidence/S1-0001/"))
        assert package.read(evidence_path) == image
        manifest = json.loads(package.read("manifest.json"))
        answer = next(row for row in manifest["files"] if row["kind"] == "complete_answer")
        assert answer["sha256"] == sha256("完整回答".encode()).hexdigest()
