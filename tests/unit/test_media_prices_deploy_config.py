from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_dataset_path_is_pinned_outside_release_snapshots() -> None:
    """Dataset artifacts must survive immutable API release rotations."""
    service = (ROOT / "deploy/production/geo-platform-v2-api.service").read_text(encoding="utf-8")

    assert "Environment=GEO_DATASETS_DIR=/home/xln/geo-system/platform-v2/.datasets" in service


def test_media_refresh_runs_in_a_dedicated_resource_controlled_service() -> None:
    service = (ROOT / "deploy/production/geo-platform-v2-media-refresh.service").read_text(
        encoding="utf-8"
    )
    path = (ROOT / "deploy/production/geo-platform-v2-media-refresh.path").read_text(
        encoding="utf-8"
    )

    assert "tools/media_prices_refresh_worker.py" in service
    assert "MemoryHigh=2G" in service
    assert "MemoryMax=3G" in service
    assert "ReadWritePaths=/home/xln/geo-system/platform-v2/.datasets" in service
    assert "LoadCredential=vault-token:/etc/geo-platform-v2/vault-runtime-token" in service
    assert "Environment=GEO_KMS_PROVIDER=vault_transit" in service
    assert (
        "GEO_VAULT_TRANSIT_TOKEN_FILE="
        "/run/credentials/geo-platform-v2-media-refresh.service/vault-token" in service
    )
    assert "media-prices.refresh.request.json" in path
    assert "Unit=geo-platform-v2-media-refresh.service" in path


def test_api_limit_covers_measured_dashboard_working_set() -> None:
    limits = (ROOT / "deploy/production/geo-platform-v2-api-limits.conf").read_text(
        encoding="utf-8"
    )

    assert "MemoryHigh=1G" in limits
    assert "MemoryMax=1536M" in limits


def test_nginx_compresses_large_json_api_responses() -> None:
    gzip = (ROOT / "deploy/production/geo-platform-v2-json-gzip.conf").read_text(encoding="utf-8")

    assert "gzip_vary on;" in gzip
    assert "gzip_types application/json application/problem+json;" in gzip
