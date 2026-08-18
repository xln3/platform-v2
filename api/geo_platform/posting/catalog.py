from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from ..config import get_settings

CatalogType = Literal["news", "wemedia"]
ProviderName = Literal[
    "prfabu",
    "toumeiw",
    "mtpfw",
    "meititejia",
    "meijiehezi",
    "pinda",
]

PROVIDERS: tuple[ProviderName, ...] = (
    "prfabu",
    "toumeiw",
    "mtpfw",
    "meititejia",
    "meijiehezi",
    "pinda",
)
_DATASETS = {
    "news": ("media-prices.json", "media-prices.sha256"),
    "wemedia": ("media-wemedia.json", "media-wemedia.sha256"),
}
_MAX_DATASET_BYTES = 64 * 1024 * 1024


class CatalogInvalid(RuntimeError):
    """The selected media target cannot be resolved from the current snapshot."""


@dataclass(frozen=True, slots=True)
class RequestedTarget:
    catalog_type: CatalogType
    provider: ProviderName
    catalog_sha256: str
    provider_media_id: str
    media_name: str
    media_platform: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    catalog_type: CatalogType
    provider: ProviderName
    media_name: str
    media_platform: str
    provider_media_id: str
    quoted_price: Decimal


@dataclass(frozen=True, slots=True)
class ResolvedCatalog:
    sha256: str
    targets: tuple[ResolvedTarget, ...]


def _datasets_dir() -> Path:
    configured = get_settings().datasets_dir
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / ".datasets"


def _read_dataset(catalog_type: CatalogType) -> tuple[dict[str, Any], str]:
    dataset_name, sidecar_name = _DATASETS[catalog_type]
    base = _datasets_dir()
    try:
        payload = (base / dataset_name).read_bytes()
    except OSError as exc:
        raise CatalogInvalid("catalog_snapshot_missing") from exc
    if not payload or len(payload) > _MAX_DATASET_BYTES:
        raise CatalogInvalid("catalog_snapshot_invalid")
    digest = hashlib.sha256(payload).hexdigest()
    try:
        expected = (base / sidecar_name).read_text(encoding="utf-8").split()[0].lower()
    except (OSError, IndexError) as exc:
        raise CatalogInvalid("catalog_snapshot_integrity_missing") from exc
    if expected != digest:
        raise CatalogInvalid("catalog_snapshot_integrity_mismatch")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CatalogInvalid("catalog_snapshot_invalid") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("rows"), list):
        raise CatalogInvalid("catalog_snapshot_invalid")
    return decoded, digest


def _media_id(row: dict[str, Any], provider: ProviderName) -> str:
    ids = row.get("ids")
    if not isinstance(ids, dict):
        return ""
    value = ids.get(provider)
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and 0 < len(value) <= 120 and value.isascii():
        return value
    return ""


def _price(row: dict[str, Any], provider: ProviderName) -> Decimal:
    prices = row.get("prices")
    value = prices.get(provider) if isinstance(prices, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise CatalogInvalid("catalog_provider_quote_missing")
    try:
        price = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise CatalogInvalid("catalog_provider_quote_invalid") from exc
    if price <= 0 or price > Decimal("1000000"):
        raise CatalogInvalid("catalog_provider_quote_invalid")
    return price


def resolve_targets(requested: list[RequestedTarget]) -> ResolvedCatalog:
    if not 1 <= len(requested) <= 50:
        raise CatalogInvalid("posting_target_count_invalid")
    grouped: dict[CatalogType, list[RequestedTarget]] = {"news": [], "wemedia": []}
    seen: set[tuple[str, str, str]] = set()
    for target in requested:
        selection_identity = (
            target.catalog_type,
            target.provider,
            target.provider_media_id,
        )
        if selection_identity in seen:
            raise CatalogInvalid("posting_target_duplicate")
        seen.add(selection_identity)
        grouped[target.catalog_type].append(target)

    resolved: list[ResolvedTarget] = []
    snapshot_digests: list[str] = []
    for catalog_type, targets in grouped.items():
        if not targets:
            continue
        dataset, digest = _read_dataset(catalog_type)
        if any(target.catalog_sha256 != digest for target in targets):
            raise CatalogInvalid("catalog_snapshot_stale")
        snapshot_digests.append(f"{catalog_type}:{digest}")
        rows: dict[tuple[ProviderName, str], dict[str, Any]] = {}
        ambiguous: set[tuple[ProviderName, str]] = set()
        requested_providers = {target.provider for target in targets}
        for raw in dataset["rows"]:
            if not isinstance(raw, dict):
                continue
            for provider in requested_providers:
                provider_media_id = _media_id(raw, provider)
                if not provider_media_id:
                    continue
                provider_identity = (provider, provider_media_id)
                if provider_identity in rows:
                    ambiguous.add(provider_identity)
                else:
                    rows[provider_identity] = raw
        for target in targets:
            provider_identity = (target.provider, target.provider_media_id)
            if provider_identity in ambiguous:
                raise CatalogInvalid("catalog_provider_target_ambiguous")
            row = rows.get(provider_identity)
            if row is None:
                raise CatalogInvalid("catalog_provider_target_not_found")
            name = row.get("name")
            platform = row.get("platform", "") if catalog_type == "wemedia" else ""
            if name != target.media_name or platform != target.media_platform:
                raise CatalogInvalid("catalog_target_identity_mismatch")
            resolved.append(
                ResolvedTarget(
                    catalog_type=target.catalog_type,
                    provider=target.provider,
                    media_name=target.media_name,
                    media_platform=target.media_platform,
                    provider_media_id=target.provider_media_id,
                    quoted_price=_price(row, target.provider),
                )
            )
    combined_digest = hashlib.sha256("\n".join(sorted(snapshot_digests)).encode()).hexdigest()
    return ResolvedCatalog(sha256=combined_digest, targets=tuple(resolved))
