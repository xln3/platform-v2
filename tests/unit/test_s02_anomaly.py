from decimal import Decimal

from domain.metrics.anomaly import detect_anomaly


def test_anomaly_explains_largest_dimension_contributions() -> None:
    result = detect_anomaly(
        historical_values=(
            Decimal("0.80"),
            Decimal("0.79"),
            Decimal("0.81"),
            Decimal("0.80"),
        ),
        observed_value=Decimal("0.40"),
        dimension_deltas={
            "model:doubao": Decimal("-0.30"),
            "region:beijing": Decimal("-0.08"),
            "mode:deep": Decimal("-0.02"),
        },
    )
    assert result.anomalous
    assert result.root_causes[0][0] == "model:doubao"
    assert result.z_score is not None
