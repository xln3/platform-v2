from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.metrics.customer import (
    CustomerAnswerFact,
    CustomerCitationFact,
    CustomerRiskFact,
    CustomerSourceAuditFact,
    assert_customer_projection_safe,
    build_customer_metric_bundle,
    infer_competitor_mentions,
    infer_recommendation,
    is_own_site,
    metric_catalog,
    own_site_host,
)


def _citation(
    host: str, *, own: bool = False, cited_text: str | None = "原文"
) -> CustomerCitationFact:
    return CustomerCitationFact(
        canonical_url=f"https://{host}/article",
        host=host,
        own_source=own,
        cited_text=cited_text,
        title=f"{host} 标题",
    )


def _facts() -> tuple[CustomerAnswerFact, ...]:
    return (
        CustomerAnswerFact(
            answer_pub_id="ans_1",
            capture_time=datetime(2026, 8, 14, 8, tzinfo=UTC),
            model="doubao",
            region="华东",
            mode="deep",
            query_pub_id="qry_1",
            query_text="推荐一个安全品牌",
            query_group="推荐场景",
            response_text="Acme 优先于 Beta。",
            mentioned=True,
            rank=1,
            sentiment="positive",
            recommended=True,
            competitor_mentions=("Beta",),
            competitor_ranks={"Beta": 2},
            citations=(
                _citation("acme.example", own=True),
                _citation("news.example"),
            ),
        ),
        CustomerAnswerFact(
            answer_pub_id="ans_2",
            capture_time=datetime(2026, 8, 14, 10, tzinfo=UTC),
            model="deepseek",
            region="华北",
            mode="quick",
            query_pub_id="qry_2",
            query_text="安全品牌怎么选",
            query_group="选型场景",
            response_text="Beta 与 Gamma 都可考虑。",
            mentioned=False,
            rank=None,
            sentiment="neutral",
            recommended=False,
            competitor_mentions=("Beta", "Gamma"),
            competitor_ranks={"Beta": 1, "Gamma": 2},
            citations=(_citation("news.example", cited_text=None),),
        ),
        CustomerAnswerFact(
            answer_pub_id="ans_3",
            capture_time=datetime(2026, 8, 16, 9, tzinfo=UTC),
            model="deepseek",
            region="华东",
            mode="deep",
            query_pub_id="qry_1",
            query_text="推荐一个安全品牌",
            query_group="推荐场景",
            response_text="Gamma 排在 Acme 前面。",
            mentioned=True,
            rank=3,
            sentiment="neutral",
            recommended=None,
            competitor_mentions=("Gamma",),
            competitor_ranks={"Gamma": 1},
            citations=(),
        ),
    )


def _metric(bundle: dict[str, object], code: str) -> float | int | None:
    metrics = bundle["metrics"]
    assert isinstance(metrics, list)
    return next(row["value"] for row in metrics if row["code"] == code)


@pytest.mark.parametrize(
    ("query_text", "response_text", "mentioned", "rank", "expected"),
    (
        ("网络安全厂商推荐", "盛邦安全可作为候选。", True, None, True),
        ("网络安全厂商推荐", "可考虑其他服务商。", False, None, False),
        ("网络安全厂商推荐", "不推荐盛邦安全，建议继续比较。", True, None, False),
        ("网络安全是什么", "盛邦安全提供相关产品。", True, None, None),
        ("网络安全是什么", "盛邦安全值得推荐。", True, None, True),
        ("安全厂商怎么选", "2. 盛邦安全", True, 2, True),
        (None, "没有涉及目标品牌。", False, None, None),
    ),
)
def test_recommendation_is_derived_from_saved_query_and_answer_facts(
    query_text: str | None,
    response_text: str,
    mentioned: bool,
    rank: int | None,
    expected: bool | None,
) -> None:
    assert (
        infer_recommendation(
            query_text=query_text,
            response_text=response_text,
            brand_name="盛邦安全",
            mentioned=mentioned,
            rank=rank,
        )
        is expected
    )


def test_customer_bundle_computes_full_business_dimensions_without_task_fields() -> None:
    bundle = build_customer_metric_bundle(
        project_pub_id="prj_safe",
        brand_name="Acme",
        competitor_names=("Beta", "Gamma"),
        answers=_facts(),
        source_audits=(
            CustomerSourceAuditFact("acme.example", "factual", "accurate", "ok", True),
            CustomerSourceAuditFact("news.example", "factual", "unsupported", "ok", False),
        ),
        risks=(
            CustomerRiskFact(
                "doubao", "", "Acme", "support", False, Decimal("0.9"), datetime.now(UTC)
            ),
            CustomerRiskFact(
                "deepseek",
                "Beta",
                "Acme",
                "negative",
                True,
                Decimal("0.8"),
                datetime.now(UTC),
            ),
        ),
        generated_at=datetime(2026, 8, 17, 0, tzinfo=UTC),
    )

    assert _metric(bundle, "answer_count") == 3
    assert _metric(bundle, "mention_count") == 2
    assert _metric(bundle, "query_count") == 2
    assert _metric(bundle, "model_count") == 2
    assert _metric(bundle, "region_count") == 2
    assert _metric(bundle, "mode_count") == 2
    assert _metric(bundle, "observation_day_count") == 2
    assert _metric(bundle, "configured_competitor_count") == 2
    assert _metric(bundle, "mention_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "recommendation_classification_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "top1_rate") == pytest.approx(1 / 3, abs=0.0001)
    assert _metric(bundle, "top3_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "average_rank") == 2
    assert _metric(bundle, "ranked_answer_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "rank_stddev") == 1
    assert _metric(bundle, "share_of_voice") == pytest.approx(1 / 3, abs=0.0001)
    assert _metric(bundle, "head_to_head_win_rate") == 0.5
    assert _metric(bundle, "head_to_head_tie_rate") == 0
    assert _metric(bundle, "head_to_head_loss_rate") == 0.5
    assert _metric(bundle, "citation_coverage") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "uncited_answer_rate") == pytest.approx(1 / 3, abs=0.0001)
    assert _metric(bundle, "mentioned_answer_citation_rate") == 0.5
    assert _metric(bundle, "top_source_share") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "own_source_answer_rate") == pytest.approx(1 / 3, abs=0.0001)
    assert _metric(bundle, "own_source_share_of_cited_answers") == 0.5
    assert _metric(bundle, "third_party_source_answer_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "citation_title_visibility_rate") == 1
    assert _metric(bundle, "sentiment_classification_rate") == 1
    assert _metric(bundle, "unknown_sentiment_rate") == 0
    assert _metric(bundle, "neutral_rate") == pytest.approx(2 / 3, abs=0.0001)
    assert _metric(bundle, "source_audit_count") == 2
    assert _metric(bundle, "source_accuracy_rate") == 0.5
    assert _metric(bundle, "source_unsupported_rate") == 0.5
    assert _metric(bundle, "risk_judgment_count") == 2
    assert _metric(bundle, "disparagement_count") == 1
    assert _metric(bundle, "support_count") == 1
    assert _metric(bundle, "disparagement_rate") == 0.5
    assert _metric(bundle, "support_rate") == 0.5
    assert {row["key"] for row in bundle["models"]} == {"doubao", "deepseek"}
    assert {row["name"] for row in bundle["competitors"]} == {"Beta", "Gamma"}
    assert_customer_projection_safe(bundle)


def test_trend_keeps_observed_dates_and_never_synthesizes_missing_zero_days() -> None:
    bundle = build_customer_metric_bundle(
        project_pub_id="prj_safe",
        brand_name="Acme",
        competitor_names=("Beta", "Gamma"),
        answers=_facts(),
        generated_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert [row["date"] for row in bundle["trends"]] == ["2026-08-14", "2026-08-16"]
    assert all(row["date"] != "2026-08-15" for row in bundle["trends"])


def test_snapshot_hash_is_deterministic_for_the_same_fact_snapshot() -> None:
    kwargs = {
        "project_pub_id": "prj_safe",
        "brand_name": "Acme",
        "competitor_names": ("Beta", "Gamma"),
        "answers": _facts(),
        "generated_at": datetime(2026, 8, 17, tzinfo=UTC),
    }
    first = build_customer_metric_bundle(**kwargs)
    second = build_customer_metric_bundle(**kwargs)

    assert first == second
    assert len(first["snapshot_hash"]) == 64


def test_unknown_sentiment_is_not_misreported_as_neutral() -> None:
    fact = replace(_facts()[0], sentiment=None)
    bundle = build_customer_metric_bundle(
        project_pub_id="prj_safe",
        brand_name="Acme",
        competitor_names=(),
        answers=(fact,),
        generated_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert _metric(bundle, "sentiment_classification_rate") == 0
    assert _metric(bundle, "unknown_sentiment_rate") == 1
    assert _metric(bundle, "neutral_rate") == 0


@pytest.mark.parametrize(
    "field",
    [
        "total_tasks",
        "completed_tasks",
        "failed_tasks",
        "success_rate",
        "workflow_id",
        "browser_instance",
        "platform_account",
        "error_code",
    ],
)
def test_customer_projection_rejects_internal_operational_fields_at_any_depth(field: str) -> None:
    with pytest.raises(ValueError, match="operational field rejected"):
        assert_customer_projection_safe({"safe": [{"nested": {field: 1}}]})


def test_catalog_and_literal_helpers_are_stable_and_safe() -> None:
    codes = [item.code for item in metric_catalog()]
    assert len(codes) == len(set(codes))
    assert len(codes) >= 60
    assert infer_competitor_mentions("beta 与 GAMMA", ("Beta", "Gamma", "Delta")) == (
        "Beta",
        "Gamma",
    )
    assert own_site_host("https://www.Acme.Example/path") == "www.acme.example"
    assert is_own_site("news.acme.example", "www.acme.example")
    assert not is_own_site("lookalike-acme.example", "www.acme.example")
