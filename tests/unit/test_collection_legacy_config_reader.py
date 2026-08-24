from __future__ import annotations

import hashlib
import json

import pytest

from domain.collection.legacy import LegacyConfigReadError, read_legacy_config_web_view
from domain.collection.surface import CollectionSurface


def _snapshot() -> str:
    return json.dumps(
        {
            "effective_at": "2026-08-01T00:00:00Z",
            "frequency": "daily",
            "models": ["doubao", "deepseek"],
            "modes": ["normal"],
            "query_groups": [{"name": "brand", "items": [{"text": "kept opaque"}]}],
            "regions": ["北京", "上海"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_reader_adds_only_web_overlay_and_preserves_source_hash() -> None:
    snapshot = _snapshot()
    snapshot_hash = hashlib.sha256(snapshot.encode()).hexdigest()

    view = read_legacy_config_web_view(
        snapshot_json=snapshot,
        snapshot_hash=snapshot_hash,
    )

    assert view.source_snapshot_hash == snapshot_hash
    assert view.source_contract_shape == "monitoring-config-v1"
    assert view.collection_surface is CollectionSurface.CONSUMER_WEB
    assert {item.collection_surface for item in view.models} == {CollectionSurface.CONSUMER_WEB}
    assert [item.legacy_model for item in view.models] == ["doubao", "deepseek"]
    assert all(item.product_variant is None for item in view.models)
    assert view.campaign_identity_state == "not_available"
    assert view.primary_slot_identity_state == "not_available"
    assert view.configured_denominator_state == "not_available"
    rendered = view.model_dump(mode="json")
    assert "query_groups" not in rendered
    assert "sample_ordinal" not in rendered
    assert "provider_api" not in json.dumps(rendered)
    assert "consumer_app" not in json.dumps(rendered)


def test_reader_rejects_hash_mismatch_without_recanonicalizing() -> None:
    with pytest.raises(LegacyConfigReadError, match="legacy_snapshot_hash_mismatch"):
        read_legacy_config_web_view(snapshot_json=_snapshot() + "\n", snapshot_hash="0" * 64)


def test_reader_rejects_unknown_legacy_shape_instead_of_guessing() -> None:
    snapshot = _snapshot()
    unknown = json.loads(snapshot)
    unknown["collection_targets"] = []
    encoded = json.dumps(unknown, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(LegacyConfigReadError, match="legacy_snapshot_schema_unknown"):
        read_legacy_config_web_view(
            snapshot_json=encoded,
            snapshot_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        )


def test_reader_supports_exact_migration_activation_shape_without_new_identity() -> None:
    snapshot = json.dumps(
        {
            "cadence": "daily",
            "legacy_enabled": False,
            "migration_activation": "review_required",
            "models": ["doubao"],
            "modes": ["normal"],
            "platforms": ["doubao"],
            "query_groups": [{"name": "legacy", "items": [{"text": "opaque"}]}],
            "regions": ["天津", "上海"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    view = read_legacy_config_web_view(
        snapshot_json=snapshot,
        snapshot_hash=hashlib.sha256(snapshot.encode()).hexdigest(),
    )

    assert view.source_contract_shape == "migration-activation-v1"
    assert view.collection_surface is CollectionSurface.CONSUMER_WEB
    assert view.models[0].product_variant is None


def test_reader_never_uses_legacy_channel_as_surface() -> None:
    legacy = json.loads(_snapshot())
    legacy["channel"] = "api"
    encoded = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(LegacyConfigReadError, match="legacy_snapshot_schema_unknown"):
        read_legacy_config_web_view(
            snapshot_json=encoded,
            snapshot_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        )
