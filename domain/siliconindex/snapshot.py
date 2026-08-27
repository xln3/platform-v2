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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

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
_SUPPORTED_SCHEMA_VERSIONS = {"1.1.0", "1.2.0"}
_OBJECT_TYPES = {
    "legal_entity",
    "group",
    "company",
    "brand",
    "brand_family",
    "sub_brand",
    "business_unit",
    "product",
    "tool",
    "institution",
}
_SCHEMA_FILES = {
    "brands": "brand.schema.json",
    "mentions": "mention.schema.json",
    "categories": "category.schema.json",
    "cognition-profiles": "cognition-profile.schema.json",
    "compliance-rules": "compliance-rule.schema.json",
    "competitor-relations": "competitor-relation.schema.json",
    "query-templates": "query-template.schema.json",
}


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
    try:
        datetime(year, month, day)
    except ValueError:
        return None
    return year, month, day, sequence


def _schema_root(root: Path) -> Path | None:
    shared_pointer = root.parent / "schema-bundles" / "1.2.0" / "CURRENT"
    shared_root: Path | None = None
    try:
        shared_digest = shared_pointer.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", shared_digest):
            shared_root = shared_pointer.parent / shared_digest / "v1"
    except OSError:
        pass
    candidates = (
        root / "schemas" / "v1",
        root.parent.parent / "schemas" / "v1",
        shared_root,
    )
    return next(
        (
            candidate
            for candidate in candidates
            if candidate is not None
            and all((candidate / filename).is_file() for filename in _SCHEMA_FILES.values())
        ),
        None,
    )


def _install_shared_schema_bundle(sync_root: Path, source: Path) -> str:
    """Install validated schemas content-addressably without mutating a release."""

    digest = hashlib.sha256()
    for filename in sorted(_SCHEMA_FILES.values()):
        digest.update(filename.encode())
        digest.update((source / filename).read_bytes())
    value = digest.hexdigest()
    base = sync_root / "schema-bundles" / "1.2.0"
    target = base / value / "v1"
    base.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        temporary = Path(tempfile.mkdtemp(prefix=f".{value}-", dir=str(base)))
        try:
            temporary_target = temporary / "v1"
            shutil.copytree(source, temporary_target)
            try:
                os.replace(temporary, target.parent)
            except OSError:
                if not target.is_dir():
                    raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    if not all((target / filename).is_file() for filename in _SCHEMA_FILES.values()):
        raise SiliconIndexSyncError("shared_schema_bundle_incomplete")
    pointer = base / ".CURRENT.tmp"
    pointer.write_text(value, encoding="utf-8")
    os.replace(pointer, base / "CURRENT")
    return value


def _validate_official_schemas(root: Path, data: dict[str, Any]) -> None:
    schema_root = _schema_root(root)
    if schema_root is None:
        raise SiliconIndexSyncError("official_schema_bundle_missing")
    for dataset_name, schema_filename in _SCHEMA_FILES.items():
        schema = _read_json(schema_root / schema_filename, 1024 * 1024)
        if not isinstance(schema, dict):
            raise SiliconIndexSyncError(f"invalid_official_schema:{schema_filename}")
        try:
            Draft7Validator.check_schema(schema)
            validator = Draft7Validator(schema)
        except SchemaError as exc:
            raise SiliconIndexSyncError(f"invalid_official_schema:{schema_filename}") from exc
        rows = data.get(dataset_name)
        if not isinstance(rows, list):
            raise SiliconIndexSyncError(f"schema_dataset_not_array:{dataset_name}")
        for index, row in enumerate(rows):
            error = next(validator.iter_errors(row), None)
            if error is not None:
                path = ".".join(str(value) for value in error.absolute_path)
                suffix = f":{path}" if path else ""
                raise SiliconIndexSyncError(
                    f"official_schema_validation_failed:{dataset_name}:{index}{suffix}"
                )


def validate_snapshot(root: Path) -> dict[str, Any]:
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("endpoints"), dict):
        raise SiliconIndexSyncError("invalid_manifest")
    release_id = str(manifest.get("release_id") or "")
    if not _SAFE_RELEASE.fullmatch(release_id):
        raise SiliconIndexSyncError("invalid_release_id")
    if _release_order(release_id) is None:
        raise SiliconIndexSyncError("unorderable_release_id")
    if manifest.get("schema_version") not in _SUPPORTED_SCHEMA_VERSIONS:
        raise SiliconIndexSyncError("unsupported_schema_version")
    data = {name: _read_json(root / f"{name}.json") for name in FILES}
    if any(not isinstance(data[name], list | dict) for name in FILES):
        raise SiliconIndexSyncError("invalid_dataset_shape")
    if manifest.get("schema_version") == "1.2.0":
        _validate_official_schemas(root, data)
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
    identity_objects: dict[str, tuple[str, dict[str, Any]]] = {}
    for brand in brands:
        for identity in brand.get("identity_objects", []):
            if not isinstance(identity, dict):
                raise SiliconIndexSyncError(f"invalid_identity_object:{brand.get('brand_id')}")
            object_id = identity.get("object_id")
            if (
                not object_id
                or object_id in identity_objects
                or identity.get("object_type") not in _OBJECT_TYPES
                or not identity.get("canonical_name")
            ):
                raise SiliconIndexSyncError(f"invalid_identity_object:{object_id}")
            if identity.get("review_status") == "reviewed" and not identity.get("evidence_urls"):
                raise SiliconIndexSyncError(f"reviewed_identity_without_evidence:{object_id}")
            identity_objects[str(object_id)] = (str(brand.get("brand_id")), identity)
    for object_id, (_brand_id, identity) in identity_objects.items():
        parent_id = identity.get("parent_object_id")
        if parent_id and parent_id not in identity_objects:
            raise SiliconIndexSyncError(f"invalid_identity_parent_ref:{object_id}")
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
        identity_object_id = mention.get("identity_object_id")
        if identity_object_id:
            identity_entry = identity_objects.get(str(identity_object_id))
            if identity_entry is None or identity_entry[0] != mention.get("brand_id"):
                raise SiliconIndexSyncError(f"invalid_identity_ref:{mention.get('mention_id')}")
            if (
                mention.get("status") == "reviewed"
                and identity_entry[1].get("review_status") != "reviewed"
            ):
                raise SiliconIndexSyncError(f"unreviewed_identity_ref:{mention.get('mention_id')}")
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
    published_brand_ids = {
        row["brand_id"]
        for row in brands
        if row.get("status") == "active" and row.get("review_status") == "reviewed"
    }
    search_index = data["search-index"]
    if not isinstance(search_index, list) or any(
        not isinstance(row, dict) or row.get("brand_id") not in published_brand_ids
        for row in search_index
    ):
        raise SiliconIndexSyncError("unpublished_search_projection")
    graph = data["graph"]
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes", []), list):
        raise SiliconIndexSyncError("invalid_graph_projection")
    if any(
        isinstance(node, dict)
        and node.get("type") == "brand"
        and node.get("id") not in published_brand_ids
        for node in graph.get("nodes", [])
    ):
        raise SiliconIndexSyncError("unpublished_graph_projection")
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
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_file_bytes = max_file_bytes
        self.clock = clock or time.time

    def current_release(self) -> str | None:
        pointer = self.root / "CURRENT"
        if not pointer.is_file():
            return None
        value = pointer.read_text(encoding="utf-8").strip()
        if not _SAFE_RELEASE.fullmatch(value) or _release_order(value) is None:
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
        history_value = dict(value)
        history_path = self.root / "sync-history.jsonl"
        previous_record_hash: str | None = None
        if history_path.is_file():
            try:
                prior_lines = [
                    line
                    for line in history_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if prior_lines:
                    prior = json.loads(prior_lines[-1])
                    candidate = prior.get("record_hash") if isinstance(prior, dict) else None
                    if not isinstance(candidate, str) or not re.fullmatch(
                        r"sha256:[0-9a-f]{64}", candidate
                    ):
                        raise ValueError("invalid_previous_record_hash")
                    previous_record_hash = candidate
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise SiliconIndexSyncError("sync_history_invalid") from exc
        history_value["previous_record_hash"] = previous_record_hash
        history_value["record_hash"] = (
            "sha256:"
            + hashlib.sha256(
                (
                    json.dumps(
                        history_value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
            ).hexdigest()
        )
        with history_path.open("a", encoding="utf-8") as history:
            history.write(json.dumps(history_value, ensure_ascii=False, sort_keys=True) + "\n")
            history.flush()
            os.fsync(history.fileno())
        temporary = self.root / ".sync-status.tmp"
        temporary.write_text(
            json.dumps(history_value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.root / "sync-status.json")

    def sync(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        previous = self.current_release()
        started = self.clock()
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
            if manifest.get("schema_version") == "1.2.0":
                schema_endpoint = endpoints.get("schemas", "/schemas/v1")
                schema_base_url = urllib.parse.urljoin(
                    self.base_url + "/",
                    str(schema_endpoint).rstrip("/") + "/",
                )
                parsed_schema_base = urllib.parse.urlsplit(schema_base_url)
                if (
                    parsed_schema_base.scheme not in {"http", "https"}
                    or (parsed_schema_base.scheme, parsed_schema_base.netloc) != allowed_origin
                ):
                    raise SiliconIndexSyncError("untrusted_schema_endpoint")
                schema_dir = work / "schemas" / "v1"
                schema_dir.mkdir(parents=True, exist_ok=True)
                for schema_filename in sorted(_SCHEMA_FILES.values()):
                    hashes[f"schema:{schema_filename}"] = _download(
                        urllib.parse.urljoin(schema_base_url, schema_filename),
                        schema_dir / schema_filename,
                        timeout=self.timeout,
                        limit=min(self.max_file_bytes, 1024 * 1024),
                    )
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
            if incoming_order is None or (previous is not None and previous_order is None):
                raise SiliconIndexSyncError("release_order_unavailable")
            if previous_order is not None and incoming_order < previous_order:
                raise SiliconIndexSyncError(f"release_rollback_rejected:{release}<{previous}")
            schema_bundle_hash = None
            if manifest.get("schema_version") == "1.2.0":
                schema_bundle_hash = _install_shared_schema_bundle(
                    self.root,
                    work / "schemas" / "v1",
                )
            metadata = {
                "release_id": release,
                "schema_version": manifest.get("schema_version"),
                "data_version": manifest.get("data_version"),
                "generated_at": manifest.get("generated_at"),
                "synced_at": self.clock(),
                "content_hash": manifest.get("content_hash"),
                "download_hashes": hashes,
                "previous_local_release_id": previous,
                "source_url": self.base_url,
                "schema_bundle_hash": schema_bundle_hash,
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
                "finished_at": self.clock(),
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
                "finished_at": self.clock(),
                "error": str(exc),
            }
            self._write_status(result)
            if isinstance(exc, SiliconIndexSyncError):
                raise
            raise SiliconIndexSyncError(str(exc)) from exc
