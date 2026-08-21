from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_browser_roles_have_distinct_https_ports_and_public_backend_hop() -> None:
    backend = (ROOT / "deploy/production/geo-platform-v2.conf").read_text(encoding="utf-8")
    backend_locations = (ROOT / "deploy/production/nginx-v2-locations.conf").read_text(
        encoding="utf-8"
    )
    frontend_http = (ROOT / "deploy/production/geo-platform-v2-frontend-http.conf").read_text(
        encoding="utf-8"
    )
    edges = (ROOT / "deploy/production/geo-platform-v2-port-edges.conf").read_text(encoding="utf-8")
    public_proxy = (ROOT / "deploy/production/nginx-v2-public-backend-proxy.conf").read_text(
        encoding="utf-8"
    )
    mtls_backend = (
        ROOT / "deploy/production/geo-platform-v2-customer-answer-backend.conf"
    ).read_text(encoding="utf-8")

    assert "listen 8443 ssl;" in backend
    for port in (8787, 8080, 8788):
        assert edges.count(f"listen {port} ssl;") == 1
        assert edges.count(f"listen [::]:{port} ssl;") == 1

    assert "server 39.105.175.14:8443;" in frontend_http
    assert "127.0.0.1" not in frontend_http
    assert "proxy_pass https://geo_platform_v2_backend_public;" in public_proxy
    assert "proxy_ssl_verify on;" in public_proxy
    assert "proxy_ssl_verify off" not in public_proxy
    assert "proxy_ssl_name geo-answer-backend.local;" in public_proxy
    assert (
        "proxy_ssl_trusted_certificate /etc/geo-platform-v2/customer-read-pki/ca.crt;"
        in public_proxy
    )
    assert "proxy_ssl_certificate /etc/geo-platform-v2/customer-read-pki/edge.crt;" in public_proxy
    assert (
        "proxy_ssl_certificate_key /etc/geo-platform-v2/customer-read-pki/edge.key;" in public_proxy
    )
    assert "127.0.0.1:8020" not in edges
    assert "ssl_verify_client on;" in mtls_backend
    assert "location /api/v2/" in mtls_backend

    customer_server = edges.split("listen 8787 ssl;", 1)[1].split("\n}\n", 1)[0]
    assert "apps/customer-web/build/client/" in customer_server
    assert "geo-platform-v2-customer-answer-edge.conf" in customer_server
    assert "https://39.105.175.14:8788$request_uri" in customer_server

    configuration_server = edges.split("listen 8080 ssl;", 1)[1].split("\n}\n", 1)[0]
    assert "return 308 /api/v2/otp/setup;" in configuration_server
    assert "smsforwarder\\.apk)$" in configuration_server
    assert "|push|" not in configuration_server
    assert "/platform/customer" not in configuration_server
    assert "/platform/operations" not in configuration_server

    operations_server = edges.split("listen 8788 ssl;", 1)[1]
    assert "apps/operations-web/build/client/" in operations_server
    assert "https://39.105.175.14:8787$request_uri" in operations_server

    assert "https://39.105.175.14:8787/platform/customer/" in backend
    assert "https://39.105.175.14:8787/platform/customer/" in backend_locations
    assert "https://39.105.175.14:8788/platform/operations/" in backend_locations


def test_every_browser_edge_uses_the_publicly_trusted_ip_certificate() -> None:
    edges = (ROOT / "deploy/production/geo-platform-v2-port-edges.conf").read_text(encoding="utf-8")

    assert edges.count("/etc/letsencrypt/live/39.105.175.14/fullchain.pem") == 3
    assert edges.count("/etc/letsencrypt/live/39.105.175.14/privkey.pem") == 3
    assert "geosys-selfsigned" not in edges
