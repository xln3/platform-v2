from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from geo_platform.analytics.router import CompetitorView


def test_competitor_response_serializes_decimal_rates_as_json_numbers() -> None:
    view = CompetitorView.model_validate(
        {
            "competitor": "奇安信",
            "mention_count": 193,
            "answer_count": 1330,
            "average_rank": Decimal("6.27"),
            "top1_count": 41,
            "top3_count": 111,
            "top10_count": 159,
            "mention_rate": Decimal("0.1451127819548872180451127820"),
            "top1_rate": Decimal("0.030827067669172932"),
            "top3_rate": Decimal("0.08345864661654136"),
            "top10_rate": Decimal("0.11954887218045113"),
            "metric_version": "competitor-aggregation-v1",
        }
    )

    payload = jsonable_encoder(view)
    assert payload["competitor"] == "奇安信"
    assert isinstance(payload["mention_rate"], float)
    assert payload["mention_rate"] > 0
