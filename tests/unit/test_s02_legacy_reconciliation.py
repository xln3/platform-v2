from domain.scoring.analyzer import CitationInput, analyze_answer
from domain.scoring.eligibility import measurement_eligible


def test_legacy_eligibility_truth_table_is_preserved_without_flask() -> None:
    valid = {
        "captcha_mode": "not_challenged",
        "geo_source": "observed_gb_code",
        "account_source": "self_pool",
        "rate_policy": "pool_burn",
        "degraded_flag": 0,
        "observed_gb_code": "110000",
    }
    assert measurement_eligible(valid)
    for key, invalid in (
        ("captcha_mode", "unsolved"),
        ("geo_source", "requested"),
        ("account_source", "unknown"),
        ("rate_policy", "burst"),
        ("degraded_flag", 1),
        ("observed_gb_code", ""),
    ):
        assert not measurement_eligible(valid | {key: invalid})


def test_legacy_rank_and_url_reconciliation() -> None:
    result = analyze_answer(
        answer_pub_id="ans_reconcile",
        text="1. Beta\n2. Acme\n普通段落再次提到 Acme。",
        brand="Acme",
        competitors=("Beta",),
        citations=(CitationInput("https://Example.com/a?source=x&utm_campaign=y&id=1"),),
        dimensions={},
    )
    assert result.fact.rank == 2
    assert result.fact.competitor_ranks == {"Beta": 1}
    assert result.citations[0]["canonical_url"] == "https://example.com/a?id=1"


def test_explained_difference_no_longer_treats_plain_mention_order_as_rank() -> None:
    result = analyze_answer(
        answer_pub_id="ans_plain_mentions",
        text="我们推荐 Acme，也可以考虑 Beta。",
        brand="Acme",
        competitors=("Beta",),
        citations=(),
        dimensions={},
    )
    assert result.fact.mentioned is True
    assert result.fact.rank is None
    # Historical ad-hoc ordering could interpret first textual mention as rank 1. V2 deliberately
    # aligns with the legacy scorer's explicit-ranking rule and records no rank without evidence.
