from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = ROOT / "deploy" / "production" / "compose.yaml"


def test_production_temporal_namespace_retention_is_explicitly_720_hours() -> None:
    compose = PRODUCTION_COMPOSE.read_text()
    temporal_service = re.search(
        r"(?ms)^  temporal:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        compose,
    )
    assert temporal_service is not None, "production compose must define the temporal service"
    assert re.search(
        r"(?m)^      DEFAULT_NAMESPACE_RETENTION: 720h\s*$",
        temporal_service.group("body"),
    ), "production Temporal retention must be an explicit 720h value"
