from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from geo_platform.config import get_settings
from geo_platform.posting.catalog import CatalogInvalid, RequestedTarget, resolve_targets


@pytest.fixture
def catalog_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))
    get_settings.cache_clear()
    payload = {
        "generated_at": "2026-08-18 10:00",
        "rows": [
            {
                "name": "同名媒体",
                "prices": {"prfabu": 88, "toumeiw": 79},
                "ids": {"prfabu": 12345, "toumeiw": "tm_9001"},
            },
            {
                "name": "同名媒体",
                "prices": {"prfabu": 99},
                "ids": {"prfabu": 67890},
            },
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(blob).hexdigest()
    (tmp_path / "media-prices.json").write_bytes(blob)
    (tmp_path / "media-prices.sha256").write_text(
        f"{digest}  media-prices.json\n",
        encoding="utf-8",
    )
    yield digest
    get_settings.cache_clear()


def _target(
    digest: str,
    *,
    catalog_sha256: str | None = None,
    provider_media_id: str = "12345",
    media_name: str = "同名媒体",
) -> RequestedTarget:
    return RequestedTarget(
        catalog_type="news",
        provider="prfabu",
        catalog_sha256=catalog_sha256 or digest,
        provider_media_id=provider_media_id,
        media_name=media_name,
        media_platform="",
    )


def test_catalog_resolves_exact_supplier_media_id_despite_duplicate_display_name(
    catalog_snapshot: str,
) -> None:
    resolved = resolve_targets([_target(catalog_snapshot)])

    assert len(resolved.targets) == 1
    assert resolved.targets[0].provider_media_id == "12345"
    assert str(resolved.targets[0].quoted_price) == "88.00"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"catalog_sha256": "0" * 64}, "catalog_snapshot_stale"),
        ({"provider_media_id": "missing"}, "catalog_provider_target_not_found"),
        ({"media_name": "被篡改名称"}, "catalog_target_identity_mismatch"),
    ],
)
def test_catalog_rejects_stale_or_tampered_cross_page_targets(
    catalog_snapshot: str,
    changes: dict[str, str],
    error: str,
) -> None:
    with pytest.raises(CatalogInvalid, match=error):
        resolve_targets([_target(catalog_snapshot, **changes)])
