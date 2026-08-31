from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_failure_projection_is_delivered_by_forward_migration() -> None:
    baseline = (ROOT / "migrations/versions/s18_0001_geo_metrics_v2.py").read_text(encoding="utf-8")
    forward = (ROOT / "migrations/versions/s18_0003_metrics_v2_failure.py").read_text(
        encoding="utf-8"
    )

    assert sha256(baseline.encode()).hexdigest() == (
        "d8cb13688c44a2395ad793fa257a0a89a3368ac60076ba5c433a7da545c60174"
    )
    assert (
        'down_revision: str | Sequence[str] | None = "s18_0002_knowledge_model_lineage"' in forward
    )
    assert "ADD COLUMN failed_answer_count" in forward
    assert "'analysis_unknown','analysis_failed'" in forward
    assert "DROP CONSTRAINT ck_semantic_judge_policy_published" in forward
    assert "tenant_pub_id,answer_pub_id,query_context_fact_pub_id" in forward
    assert "entity_dictionary_hash,decision_set_hash" in forward
    assert "NULL::TEXT AS project_pub_id" in forward
    assert "NULL::TEXT AS human_attempt_pub_id" in forward
