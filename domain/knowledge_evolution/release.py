"""Immutable, content-verified local knowledge releases and atomic activation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class KnowledgeReleaseError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (rendered + "\n").encode()


def _artifact_name(domain: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", domain).strip("-")[:60] or "domain"
    suffix = hashlib.sha256(domain.encode()).hexdigest()[:12]
    return f"domains/{prefix}-{suffix}.json"


class KnowledgeReleaseStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def current_release_id(self) -> str | None:
        pointer = self.root / "CURRENT"
        if not pointer.is_file():
            return None
        value = pointer.read_text(encoding="utf-8").strip()
        if not _SAFE_RELEASE.fullmatch(value):
            raise KnowledgeReleaseError("invalid_current_pointer")
        return value

    def previous_release_id(self) -> str | None:
        pointer = self.root / "PREVIOUS"
        if not pointer.is_file():
            return None
        value = pointer.read_text(encoding="utf-8").strip()
        if not _SAFE_RELEASE.fullmatch(value):
            raise KnowledgeReleaseError("invalid_previous_pointer")
        return value

    def _release_dir(self, release_id: str) -> Path:
        if not _SAFE_RELEASE.fullmatch(release_id):
            raise KnowledgeReleaseError("invalid_release_id")
        return self.root / release_id

    def manifest(self, release_id: str | None = None) -> dict[str, Any]:
        selected = release_id or self.current_release_id()
        if selected is None:
            raise KnowledgeReleaseError("no_active_release")
        path = self._release_dir(selected) / "manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise KnowledgeReleaseError("invalid_release_manifest") from exc
        if not isinstance(value, dict) or value.get("release_id") != selected:
            raise KnowledgeReleaseError("invalid_release_manifest")
        return value

    def verify(self, release_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest(release_id)
        release_dir = self._release_dir(str(manifest["release_id"]))
        documents: dict[str, Any] = {}
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            raise KnowledgeReleaseError("invalid_release_artifacts")
        for domain, relative in sorted(artifacts.items()):
            path = (release_dir / str(relative)).resolve()
            if release_dir.resolve() not in path.parents or not path.is_file():
                raise KnowledgeReleaseError(f"invalid_release_artifact:{domain}")
            try:
                documents[str(domain)] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise KnowledgeReleaseError(f"invalid_release_artifact:{domain}") from exc
        actual = "sha256:" + hashlib.sha256(_canonical(documents)).hexdigest()
        if actual != manifest.get("content_hash"):
            raise KnowledgeReleaseError("release_content_hash_mismatch")
        quality_report = manifest.get("quality_report")
        if not isinstance(quality_report, dict):
            raise KnowledgeReleaseError("invalid_release_quality_report")
        expected_quality_hash = manifest.get("quality_report_hash")
        if expected_quality_hash is not None:
            actual_quality_hash = "sha256:" + hashlib.sha256(_canonical(quality_report)).hexdigest()
            if actual_quality_hash != expected_quality_hash:
                raise KnowledgeReleaseError("release_quality_report_hash_mismatch")
        return manifest

    def publish(
        self,
        *,
        release_id: str,
        schema_version: str,
        documents: dict[str, Any],
        parent_release_id: str | None,
        quality_report: dict[str, Any],
        activate: bool = False,
    ) -> dict[str, Any]:
        if not documents or any(not str(key).strip() for key in documents):
            raise KnowledgeReleaseError("release_documents_required")
        self.root.mkdir(parents=True, exist_ok=True)
        final = self._release_dir(release_id)
        content_hash = "sha256:" + hashlib.sha256(_canonical(documents)).hexdigest()
        if final.exists():
            existing = self.verify(release_id)
            if existing.get("content_hash") != content_hash:
                raise KnowledgeReleaseError("immutable_release_content_mismatch")
            if activate:
                self.activate(release_id)
            return existing

        temporary = Path(tempfile.mkdtemp(prefix=".knowledge-release-", dir=str(self.root)))
        try:
            artifacts: dict[str, str] = {}
            for domain, document in sorted(documents.items()):
                relative = _artifact_name(domain)
                artifact = temporary / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(_canonical(document))
                artifacts[domain] = relative
            manifest = {
                "release_id": release_id,
                "schema_version": schema_version,
                "parent_release_id": parent_release_id,
                "content_hash": content_hash,
                "generated_at": datetime.now(UTC).isoformat(),
                "artifacts": artifacts,
                "quality_report": quality_report,
                "quality_report_hash": "sha256:"
                + hashlib.sha256(_canonical(quality_report)).hexdigest(),
            }
            (temporary / "manifest.json").write_bytes(_canonical(manifest))
            os.replace(temporary, final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self.verify(release_id)
        if activate:
            self.activate(release_id)
        return manifest

    def activate(self, release_id: str) -> dict[str, Any]:
        manifest = self.verify(release_id)
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.current_release_id()
        if current is not None and current != release_id:
            previous = self.root / ".PREVIOUS.tmp"
            previous.write_text(current, encoding="utf-8")
            os.replace(previous, self.root / "PREVIOUS")
        temporary = self.root / ".CURRENT.tmp"
        temporary.write_text(release_id, encoding="utf-8")
        os.replace(temporary, self.root / "CURRENT")
        return manifest

    def rollback(self, release_id: str) -> dict[str, Any]:
        # Rollback is pointer movement to an already verified immutable artifact.
        return self.activate(release_id)

    def load_domain_resilient(self, domain: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Read CURRENT, falling back only to the verified PREVIOUS artifact."""

        try:
            document, manifest = self.load_domain(domain)
            return document, manifest, False
        except (
            KnowledgeReleaseError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as current_error:
            previous = self.previous_release_id()
            if previous is None:
                raise KnowledgeReleaseError("no_last_known_good_release") from current_error
            document, manifest = self.load_domain(domain, previous)
            return document, manifest, True

    def load_domain(
        self, domain: str, release_id: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self.verify(release_id)
        relative = manifest["artifacts"].get(domain)
        if not isinstance(relative, str):
            raise KnowledgeReleaseError(f"domain_not_in_release:{domain}")
        path = self._release_dir(str(manifest["release_id"])) / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise KnowledgeReleaseError(f"invalid_domain_document:{domain}")
        return value, manifest

    def load_documents(
        self, release_id: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load every verified domain document from one immutable release."""

        manifest = self.verify(release_id)
        documents: dict[str, Any] = {}
        artifacts = manifest["artifacts"]
        if not isinstance(artifacts, dict):
            raise KnowledgeReleaseError("invalid_release_artifacts")
        for domain in sorted(artifacts):
            document, _ = self.load_domain(str(domain), str(manifest["release_id"]))
            documents[str(domain)] = document
        return documents, manifest
