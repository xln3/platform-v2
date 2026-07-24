from domain.scoring.analyzer import analyze_answer
from domain.scoring.eligibility import measurement_eligible


def test_unmentioned_brand_has_neutral_sentiment() -> None:
    result = analyze_answer(
        answer_pub_id="01J00000000000000000000000",
        text="另一个产品很优秀，但目标产品没有出现在回答里。",
        brand="不存在的品牌",
        competitors=(),
        citations=(),
        dimensions={},
    )
    assert result.fact.mentioned is False
    assert result.fact.sentiment == "neutral"


def test_cooperative_risk_control_pool_is_measurement_eligible() -> None:
    assert measurement_eligible(
        {
            "captcha_mode": "not_challenged",
            "geo_source": "observed_gb_code",
            "account_source": "coop_supplied_under_riskcontrol",
            "rate_policy": "pool_burn",
            "degraded_flag": 0,
            "observed_gb_code": "110000",
        }
    )
