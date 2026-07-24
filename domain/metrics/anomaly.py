from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev


@dataclass(frozen=True, slots=True)
class AnomalyResult:
    anomalous: bool
    expected_value: Decimal | None
    observed_value: Decimal
    z_score: Decimal | None
    root_causes: tuple[tuple[str, Decimal], ...]
    reason: str


def detect_anomaly(
    *,
    historical_values: Sequence[Decimal],
    observed_value: Decimal,
    dimension_deltas: Mapping[str, Decimal],
    z_threshold: Decimal = Decimal("2.5"),
) -> AnomalyResult:
    if len(historical_values) < 3:
        return AnomalyResult(
            anomalous=False,
            expected_value=None,
            observed_value=observed_value,
            z_score=None,
            root_causes=(),
            reason="insufficient historical periods",
        )
    expected = Decimal(str(mean(historical_values)))
    deviation = Decimal(str(pstdev(historical_values)))
    z_score = (
        abs(observed_value - expected) / deviation
        if deviation
        else (Decimal("999") if observed_value != expected else Decimal("0"))
    )
    root_causes = tuple(
        sorted(dimension_deltas.items(), key=lambda item: abs(item[1]), reverse=True)
    )
    return AnomalyResult(
        anomalous=z_score >= z_threshold,
        expected_value=expected,
        observed_value=observed_value,
        z_score=z_score,
        root_causes=root_causes,
        reason=(
            "observed value exceeds historical z-score threshold"
            if z_score >= z_threshold
            else "within historical variation"
        ),
    )
