from __future__ import annotations

import json

from tools.migrate_brand_observations_to_shared import _safe_context, _safe_payload


def test_legacy_observation_migration_keeps_only_controlled_safe_fields() -> None:
    context = _safe_context(
        json.dumps(
            {
                "analysis_domain": "cybersecurity",
                "comparison_scopes": ["ctid"],
                "task": "resolve",
                "project_name": "must-not-copy",
                "answer_text": "must-not-copy",
            }
        )
    )
    assert context is not None
    parsed = json.loads(context)
    assert parsed == {
        "analysis_domain": "cybersecurity",
        "comparison_scopes": ["ctid"],
        "task": "resolve",
    }

    payload = _safe_payload(
        {
            "knowledge_status": "model_inferred",
            "confidence": 0.8,
            "answer_text": "must-not-copy",
        },
        "kob_source",
    )
    assert "answer_text" not in payload
    assert payload["source_observation_hash"].startswith("sha256:")


def test_legacy_free_text_safe_context_is_not_copied() -> None:
    assert _safe_context("customer prose") is None
