"""Small HTTP SDK with a verified, opt-in last-known-good replica."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .contracts import RuntimeRequest, RuntimeResponse
from .registry import DomainRegistry
from .release import KnowledgeReleaseError, KnowledgeReleaseStore


class KnowledgeClientError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class KnowledgeHttpClient:
    """Call the stable API and optionally maintain a verified local replica.

    ``local_registry`` must contain domain packs configured to read
    ``replica_dir``. When the service is unavailable, those packs can answer
    new deterministic requests from the last verified immutable artifact.
    Without a local pack, only an exact request previously cached by this SDK
    can be returned; policy, context, prompt inputs and expected version are all
    part of that cache key.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        replica_dir: str | Path | None = None,
        local_registry: DomainRegistry | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("knowledge_client_base_url_invalid")
        if local_registry is not None and replica_dir is None:
            raise ValueError("knowledge_client_replica_dir_required")
        self.base_url = normalized
        self.headers = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.replica_dir = Path(replica_dir).resolve() if replica_dir is not None else None
        self.local_registry = local_registry

    @staticmethod
    def _body(request: RuntimeRequest) -> dict[str, Any]:
        body = asdict(request)
        body.pop("tenant", None)
        # The server derives the catalog revision after allow-list resolution;
        # callers submit only the public model identifier.
        body.pop("model_catalog_revision", None)
        body["policy"] = request.policy.value
        body["items"] = list(request.items)
        return body

    def _request_cache_path(self, request: RuntimeRequest) -> Path:
        assert self.replica_dir is not None
        key = hashlib.sha256(_canonical(self._body(request))).hexdigest()
        return self.replica_dir / "decision-cache" / f"{key}.json"

    def _cache_response(self, request: RuntimeRequest, document: dict[str, Any]) -> None:
        if self.replica_dir is None:
            return
        path = self._request_cache_path(request)
        envelope = {
            "schema_version": "knowledge-client-decision-cache-v1",
            "request_hash": _digest(self._body(request)),
            "response_hash": _digest(document),
            "response": document,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.tmp"
        temporary.write_bytes(_canonical(envelope))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _cached_response(self, request: RuntimeRequest, reason: str) -> dict[str, Any] | None:
        if self.replica_dir is None:
            return None
        path = self._request_cache_path(request)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema_version") != "knowledge-client-decision-cache-v1"
            or envelope.get("request_hash") != _digest(self._body(request))
            or not isinstance(envelope.get("response"), dict)
            or envelope.get("response_hash") != _digest(envelope["response"])
        ):
            return None
        document = dict(envelope["response"])
        release = dict(document.get("release") or {})
        release["degraded"] = True
        release["source"] = "client_verified_decision_cache"
        document["release"] = release
        document["cache_status"] = "client_last_known_good"
        document["degradation"] = list(
            dict.fromkeys([*document.get("degradation", []), reason, "exact_request_cache_only"])
        )
        return document

    def _install_replica(
        self,
        client: httpx.Client,
        request: RuntimeRequest,
        document: dict[str, Any],
    ) -> None:
        if self.replica_dir is None:
            return
        release = document.get("release")
        if not isinstance(release, dict):
            raise KnowledgeClientError("knowledge_response_release_missing")
        release_id = str(release.get("release_id") or "")
        content_hash = str(release.get("content_hash") or "")
        response = client.get(
            f"/api/v2/knowledge/v1/releases/{release_id}/replica",
            params={"namespace": request.namespace, "domain": request.domain},
        )
        if response.status_code != 200 or len(response.content) > 32 * 1024 * 1024:
            raise KnowledgeClientError("knowledge_replica_download_failed")
        try:
            replica = response.json()
        except ValueError as exc:
            raise KnowledgeClientError("knowledge_replica_invalid_json") from exc
        manifest = replica.get("manifest") if isinstance(replica, dict) else None
        documents = replica.get("documents") if isinstance(replica, dict) else None
        if not isinstance(manifest, dict) or not isinstance(documents, dict):
            raise KnowledgeClientError("knowledge_replica_invalid_shape")
        actual_hash = _digest(documents)
        if (
            manifest.get("release_id") != release_id
            or manifest.get("content_hash") != content_hash
            or actual_hash != content_hash
        ):
            raise KnowledgeClientError("knowledge_replica_hash_mismatch")
        store = KnowledgeReleaseStore(self.replica_dir)
        installed = store.publish(
            release_id=release_id,
            schema_version=str(manifest.get("schema_version") or "unknown"),
            documents={str(key): value for key, value in documents.items()},
            parent_release_id=(
                str(manifest["parent_release_id"])
                if manifest.get("parent_release_id") is not None
                else None
            ),
            quality_report=dict(manifest.get("quality_report") or {}),
            activate=True,
        )
        if installed.get("content_hash") != content_hash:
            raise KnowledgeClientError("knowledge_replica_install_mismatch")

    def _local_pack_response(
        self,
        request: RuntimeRequest,
        reason: str,
        started: float,
    ) -> dict[str, Any] | None:
        if self.local_registry is None:
            return None
        try:
            pack = self.local_registry.get(request.domain)
            release = replace(pack.release_ref(request), degraded=True)
            deterministic = tuple(
                replace(
                    decision,
                    knowledge_release_id=release.release_id,
                    knowledge_content_hash=release.content_hash,
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                )
                for decision in pack.deterministic_resolve(request)
            )
        except (KeyError, KnowledgeReleaseError, OSError, UnicodeError, ValueError):
            return None
        response = RuntimeResponse(
            request_id=request.request_id,
            domain=request.domain,
            task=request.task,
            policy=request.policy,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            release=release,
            decisions=deterministic,
            model_hypotheses=(),
            prompt_id=None,
            prompt_version=None,
            model_provider=None,
            model_name=None,
            model_version=None,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            cache_status="client_local_replica",
            degradation=(reason, "local_replica_deterministic_only"),
            observation_count=0,
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
                "model_latency_ms": 0,
            },
        )
        rendered = json.loads(json.dumps(asdict(response), ensure_ascii=False))
        if not isinstance(rendered, dict):
            raise KnowledgeClientError("knowledge_local_response_invalid")
        return rendered

    def _fallback(
        self,
        request: RuntimeRequest,
        reason: str,
        started: float,
    ) -> dict[str, Any]:
        local = self._local_pack_response(request, reason, started)
        if local is not None:
            return local
        cached = self._cached_response(request, reason)
        if cached is not None:
            return cached
        raise KnowledgeClientError(reason)

    def resolve(self, request: RuntimeRequest) -> dict[str, Any]:
        body = self._body(request)
        started = time.perf_counter()
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout_seconds,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.post("/api/v2/knowledge/v1/runtime/resolve", json=body)
                if response.status_code == 200:
                    if len(response.content) > 4 * 1024 * 1024:
                        raise KnowledgeClientError("knowledge_response_too_large")
                    try:
                        document = response.json()
                    except ValueError as exc:
                        raise KnowledgeClientError("knowledge_response_invalid_json") from exc
                    if not isinstance(document, dict) or not isinstance(
                        document.get("decisions"), list
                    ):
                        raise KnowledgeClientError("knowledge_response_invalid_shape")
                    try:
                        self._install_replica(client, request, document)
                    except KnowledgeClientError:
                        # A replica refresh must not turn a valid live decision into
                        # a request failure. The prior verified replica stays active.
                        pass
                    self._cache_response(request, document)
                    return document
        except httpx.TimeoutException:
            return self._fallback(request, "knowledge_service_timeout", started)
        except httpx.HTTPError:
            return self._fallback(request, "knowledge_service_transport_error", started)
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            return self._fallback(request, "knowledge_service_unavailable", started)
        code = "knowledge_service_error"
        try:
            error_document = response.json()
            candidate = error_document.get("error", {}).get("code")
            if isinstance(candidate, str) and candidate.isascii():
                code = candidate[:120]
        except (TypeError, ValueError):
            pass
        raise KnowledgeClientError(code)

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.get("/api/v2/knowledge/v1/health")
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise KnowledgeClientError("knowledge_health_unavailable") from exc
        if not isinstance(document, dict):
            raise KnowledgeClientError("knowledge_health_invalid_shape")
        return document


__all__ = ["KnowledgeClientError", "KnowledgeHttpClient"]
