from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_dataset_path_is_pinned_outside_release_snapshots() -> None:
    """Dataset artifacts must survive immutable API release rotations."""
    service = (ROOT / "deploy/production/geo-platform-v2-api.service").read_text(encoding="utf-8")

    assert "Environment=GEO_DATASETS_DIR=/home/xln/geo-system/platform-v2/.datasets" in service
