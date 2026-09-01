from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_failure_projection_is_delivered_by_forward_migration() -> None:
    baseline = (ROOT / "migrations/versions/s18_0001_geo_metrics_v2.py").read_text(encoding="utf-8")
    forward = (ROOT / "migrations/versions/s18_0003_metrics_v2_failure.py").read_text(
        encoding="utf-8"
    )

    assert sha256(baseline.encode()).hexdigest() == (
        "76959011e7b076314a6c7f0b72a1d7724d2779dee96b2da69453a239858b736f"
    )
    assert (
        'down_revision: str | Sequence[str] | None = "s18_0002_knowledge_model_lineage"' in forward
    )
    assert "ADD COLUMN failed_answer_count" in forward
    assert "'analysis_unknown','analysis_failed'" in forward
    assert "DROP CONSTRAINT ck_semantic_judge_policy_published" in forward
    assert "tenant_pub_id,answer_pub_id,input_hash,extractor_bundle_hash" in forward
    assert "entity_dictionary_hash,decision_set_hash" in forward
    assert "NULL::TEXT AS project_pub_id" in forward
