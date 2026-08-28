from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_customer_web_redirects_missing_trailing_slash_to_canonical_route() -> None:
    locations = (ROOT / "deploy/production/nginx-v2-locations.conf").read_text(encoding="utf-8")
    edges = (ROOT / "deploy/production/geo-platform-v2-port-edges.conf").read_text(encoding="utf-8")

    assert "listen 8787 ssl;" in edges
    customer_server = edges.split("listen 8787 ssl;", 1)[1].split("\n}\n", 1)[0]
    assert "location = /platform/customer {" in customer_server
    assert "return 308 /platform/customer/;" in customer_server
    assert "alias /opt/geo-platform-v2/current/apps/customer-web/build/client/;" in customer_server
    assert "/home/xln/geo-system/platform-v2/apps/" not in locations + edges
    assert "location = /platform/customer/login {" in customer_server
    assert (
        "alias /opt/geo-platform-v2/current/deploy/production/customer-login.html;"
        in customer_server
    )
    assert "default_type text/html;" in customer_server
    assert "charset utf-8;" in customer_server
    assert "location = /platform/customer/login/ {" in customer_server
    assert "location = /platform/operations/login {" in customer_server
    assert "return 308 /platform/customer/login$is_args$args;" in customer_server
    assert "return 308 https://39.105.175.14:8787/platform/customer/;" in locations
