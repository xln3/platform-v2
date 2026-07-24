import json

import pytest

from tools.reconciliation.compare import compare, markdown


def snapshot(value: int = 1) -> dict[str, object]:
    return {
        "task_matrix": [{"key": "task-1", "count": value}],
        "answers": [{"key": "answer-1", "content_hash": f"hash-{value}"}],
        "eligibility": [{"key": "answer-1", "eligible": True}],
        "citations": [{"key": "citation-1", "canonical_hash": "safe"}],
        "kpis": [{"key": "mention_rate", "numerator": value, "denominator": 1}],
        "reports": [{"key": "report-1", "fact_hash": "safe"}],
        "evidence": [{"key": "evidence-1", "sha256": "safe"}],
    }


def test_reconciliation_fails_unapproved_without_emitting_values() -> None:
    legacy = snapshot()
    v2 = snapshot(2)

    result = compare(legacy, v2)
    serialized = json.dumps(result)

    assert result["summary"] == {
        "differences": 3,
        "approved": 0,
        "unapproved": 3,
        "passed": False,
    }
    assert "answer-1" not in serialized
    assert "hash-1" not in serialized
    assert "hash-2" not in serialized
    assert "Result: FAIL" in markdown(result)


def test_reconciliation_accepts_explicit_hash_scoped_approval() -> None:
    legacy = snapshot()
    v2 = snapshot()
    result = compare(legacy, v2)
    assert result["summary"]["passed"] is True


def test_reconciliation_rejects_secret_bearing_input_keys() -> None:
    legacy = snapshot()
    legacy["answers"] = [{"key": "answer-1", "cookie": "never"}]
    with pytest.raises(ValueError, match="secret-bearing"):
        compare(legacy, snapshot())
