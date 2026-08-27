"""Atomically synchronize and validate the shared SiliconIndex release.

Ranking requests never call the remote service.  An operator/timer downloads one
published release here, validates references and the publisher's content hash, then
atomically advances ``CURRENT``.  Every project therefore consumes the same pinned
release and a failed refresh keeps the last good snapshot available.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CORE_FILES = (
    "brands",
    "mentions",
    "categories",
    "cognition-profiles",
    "compliance-rules",
    "competitor-relations",
    "query-templates",
)
DERIVED_FILES = ("search-index", "graph")
FILES = (*CORE_FILES, *DERIVED_FILES)
MAX_FILE_BYTES = 16 * 1024 * 1024
_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ORDERED_RELEASE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.(\d+)$")


class SiliconIndexSyncError(RuntimeError):
    """A remote release could not be safely promoted to the local snapshot."""


def _read_json(path: Path, limit: int = MAX_FILE_BYTES) -> Any:
    try:
        if path.stat().st_size > limit:
            raise SiliconIndexSyncError(f"file_too_large:{path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, SiliconIndexSyncError):
            raise
        raise SiliconIndexSyncError(f"invalid_json:{path.name}:{exc}") from exc


def _download(url: str, path: Path, *, timeout: float, limit: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "GeoPlatform-SiliconIndex/2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise SiliconIndexSyncError(f"response_too_large:{path.name}")
            digest = hashlib.sha256()
            total = 0
            with path.open("wb") as output:
                while True:
                    chunk = response.read(min(65536, limit + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise SiliconIndexSyncError(f"response_too_large:{path.name}")
                    digest.update(chunk)
                    output.write(chunk)
            return digest.hexdigest()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        if isinstance(exc, SiliconIndexSyncError):
            raise
        raise SiliconIndexSyncError(f"download_failed:{path.name}:{exc}") from exc


def _content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for name in CORE_FILES:
        digest.update((root / f"{name}.json").read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _release_order(value: str) -> tuple[int, int, int, int] | None:
    match = _ORDERED_RELEASE.fullmatch(value)
    if match is None:
        return None
    year, month, day, sequence = (int(part) for part in match.groups())
    return year, month, day, sequence


def validate_snapshot(root: Path) -> dict[str, Any]:
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("endpoints"), dict):
        raise SiliconIndexSyncError("invalid_manifest")
    release_id = str(manifest.get("release_id") or "")
    if not _SAFE_RELEASE.fullmatch(release_id):
        raise SiliconIndexSyncError("invalid_release_id")
    data = {name: _read_json(root / f"{name}.json") for name in FILES}
    if any(not isinstance(data[name], list | dict) for name in FILES):
        raise SiliconIndexSyncError("invalid_dataset_shape")
    brands = data["brands"]
    mentions = data["mentions"]
    categories = data["categories"]
    relations = data["competitor-relations"]
    rules = data["compliance-rules"]
    if not all(
        isinstance(row, dict)
        for collection in (brands, mentions, categories, relations, rules)
        for row in collection
    ):
        raise SiliconIndexSyncError("dataset_item_not_object")
    brand_ids = {row.get("brand_id") for row in brands if row.get("brand_id")}
    category_ids = {row.get("category_id") for row in categories if row.get("category_id")}
    rule_ids = {row.get("rule_id") for row in rules if row.get("rule_id")}
    if len(brand_ids) != len(brands):
        raise SiliconIndexSyncError("duplicate_or_missing_brand_id")
    if len(category_ids) != len(categories):
        raise SiliconIndexSyncError("duplicate_or_missing_category_id")
    if len(rule_ids) != len(rules):
        raise SiliconIndexSyncError("duplicate_or_missing_rule_id")
    mention_ids: set[Any] = set()
    for mention in mentions:
        if (
            not mention.get("mention_id")
            or mention.get("brand_id") not in brand_ids
            or not mention.get("text")
        ):
            raise SiliconIndexSyncError(f"invalid_mention_ref:{mention.get('mention_id')}")
        if mention["mention_id"] in mention_ids:
            raise SiliconIndexSyncError(f"duplicate_mention_id:{mention['mention_id']}")
        mention_ids.add(mention["mention_id"])
        if mention.get("match_mode") not in {
            "exact",
            "normalized_exact",
            "context_required",
            "manual_only",
        }:
            raise SiliconIndexSyncError(f"invalid_match_mode:{mention.get('mention_id')}")
    relation_ids: set[Any] = set()
    for relation in relations:
        if (
            not relation.get("relation_id")
            or relation.get("source_brand_id") not in brand_ids
            or relation.get("target_brand_id") not in brand_ids
        ):
            raise SiliconIndexSyncError(f"invalid_competitor_ref:{relation.get('relation_id')}")
        if relation["relation_id"] in relation_ids:
            raise SiliconIndexSyncError(f"duplicate_relation_id:{relation['relation_id']}")
        relation_ids.add(relation["relation_id"])
    for brand in brands:
        if any(value not in category_ids for value in brand.get("category_ids", [])):
            raise SiliconIndexSyncError(f"invalid_category_ref:{brand.get('brand_id')}")
        if any(value not in rule_ids for value in brand.get("compliance_rule_ids", [])):
            raise SiliconIndexSyncError(f"invalid_rule_ref:{brand.get('brand_id')}")
    actual_hash = _content_hash(root)
    expected_hash = str(manifest.get("content_hash") or "")
    if not expected_hash or actual_hash != expected_hash:
        raise SiliconIndexSyncError("content_hash_mismatch")
    return manifest


class SiliconIndexSynchronizer:
    def __init__(
        self,
        root: str | Path,
        base_url: str = "https://siliconindex-consumer.onrender.com/data/v1",
        *,
        timeout: float = 20,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_file_bytes = max_file_bytes

    def current_release(self) -> str | None:
        pointer = self.root / "CURRENT"
        if not pointer.is_file():
            return None
        value = pointer.read_text(encoding="utf-8").strip()
        if not _SAFE_RELEASE.fullmatch(value):
            raise SiliconIndexSyncError("invalid_current_pointer")
        return value

    def status(self) -> dict[str, Any]:
        path = self.root / "sync-status.json"
        if not path.is_file():
            return {"status": "never", "current": self.current_release()}
        try:
            value = _read_json(path, 256 * 1024)
            return value if isinstance(value, dict) else {"status": "invalid_status"}
        except SiliconIndexSyncError as exc:
            return {
                "status": "invalid_status",
                "current": self.current_release(),
                "error": str(exc),
            }

    def _write_status(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / ".sync-status.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.root / "sync-status.json")

    def sync(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        previous = self.current_release()
        started = time.time()
        work = Path(tempfile.mkdtemp(prefix="siliconindex-", dir=str(self.root)))
        try:
            hashes = {
                "manifest": _download(
                    f"{self.base_url}/manifest.json",
                    work / "manifest.json",
                    timeout=self.timeout,
                    limit=self.max_file_bytes,
                )
            }
            manifest = _read_json(work / "manifest.json")
            endpoints = manifest.get("endpoints", {}) if isinstance(manifest, dict) else {}
            configured_origin = urllib.parse.urlsplit(self.base_url)
            allowed_origin = (configured_origin.scheme, configured_origin.netloc)
            for name in FILES:
                endpoint_key = name.replace("-", "_")
                endpoint = endpoints.get(endpoint_key, f"/data/v1/{name}.json")
                url = urllib.parse.urljoin(self.base_url + "/", str(endpoint))
                parsed = urllib.parse.urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or (
                        parsed.scheme,
                        parsed.netloc,
                    )
                    != allowed_origin
                ):
                    raise SiliconIndexSyncError(f"untrusted_endpoint:{name}")
                hashes[name] = _download(
                    url,
                    work / f"{name}.json",
                    timeout=self.timeout,
                    limit=self.max_file_bytes,
                )
            manifest = validate_snapshot(work)
            release = str(manifest["release_id"])
            previous_order = _release_order(previous) if previous else None
            incoming_order = _release_order(release)
            if previous_order is not None and incoming_order is not None:
                if incoming_order < previous_order:
                    raise SiliconIndexSyncError(f"release_rollback_rejected:{release}<{previous}")
            metadata = {
                "release_id": release,
                "schema_version": manifest.get("schema_version"),
                "data_version": manifest.get("data_version"),
                "generated_at": manifest.get("generated_at"),
                "synced_at": time.time(),
                "content_hash": manifest.get("content_hash"),
                "download_hashes": hashes,
                "previous_local_release_id": previous,
                "source_url": self.base_url,
            }
            (work / "snapshot-meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            final = self.root / release
            if final.exists():
                existing_manifest = validate_snapshot(final)
                if existing_manifest.get("content_hash") != manifest.get("content_hash"):
                    raise SiliconIndexSyncError("existing_release_content_mismatch")
                shutil.rmtree(work)
            else:
                os.replace(work, final)
            pointer = self.root / ".CURRENT.tmp"
            pointer.write_text(release, encoding="utf-8")
            os.replace(pointer, self.root / "CURRENT")
            result = {
                "status": "success",
                "current": release,
                "previous": previous,
                "started_at": started,
                "finished_at": time.time(),
                "content_hash": manifest.get("content_hash"),
            }
            self._write_status(result)
            return result
        except Exception as exc:
            shutil.rmtree(work, ignore_errors=True)
            result = {
                "status": "failed",
                "current": previous,
                "started_at": started,
                "finished_at": time.time(),
                "error": str(exc),
            }
            self._write_status(result)
            if isinstance(exc, SiliconIndexSyncError):
                raise
            raise SiliconIndexSyncError(str(exc)) from exc
