from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import quote, urlencode, urlsplit

import httpx

from domain.evidence.dlp import DlpResult, redact_bytes


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    byte_size: int
    mime_type: str
    dlp_findings: tuple[str, ...]
    metadata_pub_id: str | None = None


class ContentAddressedObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "geo-evidence",
        region: str = "us-east-1",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region

    def ensure_bucket(self) -> None:
        response = self._request("PUT", f"/{self.bucket}", payload=b"")
        if response.status_code not in (200, 204, 409):
            response.raise_for_status()

    def put_redacted(
        self, payload: bytes, *, mime_type: str, namespace: str = "sha256"
    ) -> StoredObject:
        dlp = redact_bytes(payload, mime_type=mime_type)
        key = f"{namespace}/{dlp.sha256[:2]}/{dlp.sha256[2:4]}/{dlp.sha256}"
        response = self._request(
            "PUT",
            f"/{self.bucket}/{key}",
            payload=dlp.redacted,
            headers={
                "content-type": mime_type,
                "x-amz-meta-sha256": dlp.sha256,
                "x-amz-meta-dlp-findings": ",".join(dlp.findings),
            },
        )
        response.raise_for_status()
        return StoredObject(key, dlp.sha256, len(dlp.redacted), mime_type, dlp.findings)

    def get_verified(self, key: str, expected_sha256: str) -> bytes:
        response = self._request("GET", f"/{self.bucket}/{key}")
        response.raise_for_status()
        actual = sha256(response.content).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise ValueError("object integrity verification failed")
        return response.content

    def delete(self, key: str) -> None:
        response = self._request("DELETE", f"/{self.bucket}/{key}")
        response.raise_for_status()

    def presign_get(self, key: str, *, expires_seconds: int = 300) -> str:
        if not 1 <= expires_seconds <= 900:
            raise ValueError("evidence URLs must be short-lived (1..900 seconds)")
        now = datetime.now(UTC)
        date = now.strftime("%Y%m%d")
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        credential = f"{self.access_key}/{date}/{self.region}/s3/aws4_request"
        host = urlsplit(self.endpoint).netloc
        path = f"/{self.bucket}/{key}"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": credential,
            "X-Amz-Date": timestamp,
            "X-Amz-Expires": str(expires_seconds),
            "X-Amz-SignedHeaders": "host",
        }
        query = urlencode(sorted(params.items()), quote_via=quote)
        canonical = f"GET\n{path}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
        scope = f"{date}/{self.region}/s3/aws4_request"
        to_sign = (
            f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{sha256(canonical.encode()).hexdigest()}"
        )
        params["X-Amz-Signature"] = hmac.new(
            self._signing_key(date), to_sign.encode(), sha256
        ).hexdigest()
        return f"{self.endpoint}{path}?{urlencode(sorted(params.items()), quote_via=quote)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        now = datetime.now(UTC)
        date = now.strftime("%Y%m%d")
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        host = urlsplit(self.endpoint).netloc
        payload_hash = sha256(payload).hexdigest()
        request_headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": timestamp,
            **{key.lower(): value for key, value in (headers or {}).items()},
        }
        signed_header_names = sorted(request_headers)
        canonical_headers = "".join(
            f"{key}:{request_headers[key].strip()}\n" for key in signed_header_names
        )
        canonical_request = (
            f"{method}\n{path}\n\n{canonical_headers}\n"
            f"{';'.join(signed_header_names)}\n{payload_hash}"
        )
        scope = f"{date}/{self.region}/s3/aws4_request"
        to_sign = (
            f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n"
            f"{sha256(canonical_request.encode()).hexdigest()}"
        )
        signature = hmac.new(self._signing_key(date), to_sign.encode(), sha256).hexdigest()
        request_headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope},"
            f"SignedHeaders={';'.join(signed_header_names)},Signature={signature}"
        )
        return httpx.request(
            method,
            f"{self.endpoint}{path}",
            content=payload,
            headers=request_headers,
            timeout=15,
            trust_env=False,
        )

    def _signing_key(self, date: str) -> bytes:
        date_key = hmac.new(f"AWS4{self.secret_key}".encode(), date.encode(), sha256).digest()
        region_key = hmac.new(date_key, self.region.encode(), sha256).digest()
        service_key = hmac.new(region_key, b"s3", sha256).digest()
        return hmac.new(service_key, b"aws4_request", sha256).digest()


def dlp_object(payload: bytes, mime_type: str) -> DlpResult:
    return redact_bytes(payload, mime_type=mime_type)
