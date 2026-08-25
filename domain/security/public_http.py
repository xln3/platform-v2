"""Fail-closed public HTTP retrieval with redirect and connected-peer checks."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str, int], Sequence[tuple[Any, ...]]]
PeerIpLoader = Callable[[httpx.Response], str]


class PublicHttpRejected(ValueError):
    """The target cannot be proven to be a public HTTP(S) resource."""


@dataclass(frozen=True, slots=True)
class PublicHttpDocument:
    requested_url: str
    final_url: str
    payload: bytes
    mime_type: str
    http_status: int
    redirect_chain: tuple[str, ...]
    peer_ip: str


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise PublicHttpRejected("public_url_dns_answer_invalid") from exc
    if not address.is_global:
        raise PublicHttpRejected("public_url_non_global_address")
    return address


def validate_public_http_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    """Validate syntax and require every DNS answer to be globally routable."""

    candidate = url.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise PublicHttpRejected("public_url_invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port not in {80, 443})
    ):
        raise PublicHttpRejected("public_url_invalid")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise PublicHttpRejected("public_url_non_global_address")
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        _public_ip(str(literal))
        return candidate
    try:
        answers = resolver(host, port or (443 if parsed.scheme.lower() == "https" else 80))
    except OSError as exc:
        raise PublicHttpRejected("public_url_dns_failed") from exc
    addresses = {
        str(sockaddr[0])
        for answer in answers
        if len(answer) >= 5 and isinstance((sockaddr := answer[4]), tuple) and sockaddr
    }
    if not addresses:
        raise PublicHttpRejected("public_url_dns_empty")
    for address in addresses:
        _public_ip(address)
    return candidate


def _connected_peer_ip(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        raise PublicHttpRejected("public_url_peer_unobservable")
    socket_object = stream.get_extra_info("socket")
    if socket_object is None or not hasattr(socket_object, "getpeername"):
        raise PublicHttpRejected("public_url_peer_unobservable")
    try:
        peer = socket_object.getpeername()
    except OSError as exc:
        raise PublicHttpRejected("public_url_peer_unobservable") from exc
    if not isinstance(peer, tuple) or not peer:
        raise PublicHttpRejected("public_url_peer_unobservable")
    return str(_public_ip(str(peer[0])))


def fetch_public_http(
    url: str,
    *,
    client: httpx.Client,
    resolver: Resolver = socket.getaddrinfo,
    peer_ip_loader: PeerIpLoader = _connected_peer_ip,
    max_redirects: int = 5,
    max_bytes: int = 5_242_880,
) -> PublicHttpDocument:
    """Retrieve a bounded document while validating every redirect and live peer."""

    if max_redirects < 0 or not 1 <= max_bytes <= 52_428_800:
        raise ValueError("public_http_fetch_bounds_invalid")
    current = validate_public_http_url(url, resolver=resolver)
    redirect_chain: list[str] = []
    for _hop in range(max_redirects + 1):
        with client.stream("GET", current, follow_redirects=False) as response:
            peer_ip = str(_public_ip(peer_ip_loader(response)))
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise PublicHttpRejected("public_url_redirect_missing_location")
                redirect_chain.append(current)
                current = validate_public_http_url(urljoin(current, location), resolver=resolver)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise PublicHttpRejected(f"public_url_http_{response.status_code}")
            raw_length = response.headers.get("content-length")
            if raw_length:
                try:
                    if int(raw_length) > max_bytes:
                        raise PublicHttpRejected("public_url_payload_too_large")
                except ValueError as exc:
                    raise PublicHttpRejected("public_url_content_length_invalid") from exc
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise PublicHttpRejected("public_url_payload_too_large")
                chunks.append(chunk)
            mime_type = response.headers.get("content-type", "application/octet-stream")
            mime_type = mime_type.split(";", 1)[0].strip().lower()
            if not (
                mime_type.startswith("text/")
                or mime_type in {"application/json", "application/pdf", "application/xhtml+xml"}
            ):
                raise PublicHttpRejected("public_url_content_type_not_evidence")
            return PublicHttpDocument(
                requested_url=url,
                final_url=str(response.url),
                payload=b"".join(chunks),
                mime_type=mime_type,
                http_status=response.status_code,
                redirect_chain=tuple(redirect_chain),
                peer_ip=peer_ip,
            )
    raise PublicHttpRejected("public_url_redirect_limit_exceeded")


__all__ = [
    "PublicHttpDocument",
    "PublicHttpRejected",
    "fetch_public_http",
    "validate_public_http_url",
]
