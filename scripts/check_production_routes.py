"""Certify that production ingress serves only GEO Platform V2 runtime surfaces."""

import hashlib
import json
import re
from pathlib import Path

platform_root = Path(__file__).parents[1]
manifest = json.loads(
    (platform_root / "contracts" / "production-route-manifest.json").read_text(encoding="utf-8")
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


for artifact in (
    "deployment_template",
    "v2_include",
    "customer_answer_http",
    "customer_answer_edge",
    "customer_answer_backend",
):
    contract = manifest[artifact]
    path = platform_root / contract["path"]
    if sha256(path) != contract["sha256"]:
        raise SystemExit(f"Production route artifact drifted: {contract['path']}")

template = (platform_root / manifest["deployment_template"]["path"]).read_text(encoding="utf-8")
include = (platform_root / manifest["v2_include"]["path"]).read_text(encoding="utf-8")
answer_http = (platform_root / manifest["customer_answer_http"]["path"]).read_text(encoding="utf-8")
answer_edge = (platform_root / manifest["customer_answer_edge"]["path"]).read_text(encoding="utf-8")
answer_backend = (platform_root / manifest["customer_answer_backend"]["path"]).read_text(
    encoding="utf-8"
)
combined = f"{template}\n{include}\n{answer_http}\n{answer_edge}\n{answer_backend}"

if template.count("include /etc/nginx/snippets/geo-platform-v2.conf;") != 1:
    raise SystemExit("The V2 Nginx locations must be included exactly once")
if "proxy_pass http://127.0.0.1:8010" in combined:
    raise SystemExit("Production ingress must not depend on the retired runtime")
for marker in (
    "server 39.105.175.14:8443",
    "proxy_ssl_verify on",
    "proxy_ssl_certificate ",
    "ssl_verify_client on",
    "proxy_max_temp_file_size 0",
    'add_header Cache-Control "private, no-store" always',
    "geo-platform-v2-customer-answer-edge.conf",
):
    if marker not in combined:
        raise SystemExit(f"Customer answer public-hop boundary missing: {marker}")
if "/answer-library" not in answer_edge or "/relations" not in answer_edge:
    raise SystemExit("Customer answer edge allowlist is incomplete")
if "$request_uri" in answer_http + answer_edge + answer_backend or "$args" in (
    answer_http + answer_edge + answer_backend
):
    raise SystemExit("Customer answer transport logs must not include query text")
if re.search(r"location\s+/api/\s*\{(?![^}]*return\s+404)", template, re.DOTALL):
    raise SystemExit("Non-V2 API routes must fail closed")
for route in manifest["required_routes"]:
    if route not in combined:
        raise SystemExit(f"Required V2 production route missing: {route}")
for marker in manifest["forbidden_markers"]:
    if marker in combined:
        raise SystemExit(f"Retired runtime marker found in production ingress: {marker}")

print("Production route guard passed: ingress has one V2 runtime and no retired upstream.")
