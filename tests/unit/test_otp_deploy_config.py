from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_otp_paths_are_pinned_outside_release_snapshots() -> None:
    """Mutable OTP state and ignored APK binaries must survive immutable releases."""
    drop_in = (ROOT / "deploy/production/geo-platform-v2-otp-apk.conf").read_text(encoding="utf-8")

    assert (
        "GEO_OTP_INBOX_DIR=/home/xln/geo-system/platform-v2/runtime/otp_inbox" in drop_in
    )
    assert (
        "GEO_OTP_REGISTRY_PATH="
        "/home/xln/geo-system/platform-v2/runtime/otp_registered_numbers.json" in drop_in
    )
    assert "GEO_OTP_APK_PATH=/home/xln/geo-system/platform-v2/runtime/smsforwarder.apk" in drop_in
    assert "GEO_OTP_APK_VERSION=3.5.0.260224" in drop_in
    assert (
        "GEO_OTP_APK_SHA256=5dd85b9a6d2e954f8aa86713a0abb92f1a8b5e70ee5322706aab7cef1bcbb752"
        in drop_in
    )
    assert (
        "GEO_OTP_APK_SIGNER_SHA256="
        "DA:6F:81:83:14:CF:0F:4B:6A:D6:90:94:A0:39:60:F9:"
        "C7:E5:3C:CF:3B:9F:3B:7A:FA:4B:33:48:AD:6B:47:FD" in drop_in
    )
    assert "GEO_PUBLIC_BASE_URL=https://39.105.175.14:8443" in drop_in
    assert ".deploy-backups" not in drop_in

    api_service = (ROOT / "deploy/production/geo-platform-v2-api.service").read_text(
        encoding="utf-8"
    )
    assert "ReadWritePaths=/home/xln/geo-system/platform-v2" in api_service


def test_operator_routes_are_separate_fail_closed_and_rate_limited() -> None:
    server = (ROOT / "deploy/production/geo-platform-v2.conf").read_text(encoding="utf-8")
    locations = (ROOT / "deploy/production/nginx-v2-locations.conf").read_text(encoding="utf-8")
    api_service = (ROOT / "deploy/production/geo-platform-v2-api.service").read_text(
        encoding="utf-8"
    )

    assert "geo_otp_operator_ui:10m rate=30r/m" in server
    assert "geo_otp_operator_latest:10m rate=2r/s" in server
    assert "log_format geo_otp_operator" in server
    log_format = server.split("log_format geo_otp_operator", 1)[1].split(";", 1)[0]
    assert "$uri" in log_format
    assert "$request " not in log_format and "$args" not in log_format
    geo_block = server.split("geo $geo_otp_operator_allowed {", 1)[1].split("}", 1)[0]
    assert "default 0;" in geo_block
    assert "127.0.0.1/32 1;" in geo_block and "::1/128 1;" in geo_block

    protected = {
        "/api/v2/otp/setup": "geo_otp_operator_ui",
        "/api/v2/otp/setup-info": "geo_otp_operator_ui",
        "/api/v2/otp/status": "geo_otp_operator_ui",
        "/api/v2/otp/register": "geo_otp_operator_ui",
        "/api/v2/otp/latest": "geo_otp_operator_latest",
    }
    for path, zone in protected.items():
        marker = f"location = {path} {{"
        assert locations.count(marker) == 1
        block = locations.split(marker, 1)[1].split("\n}", 1)[0]
        assert "if ($geo_otp_operator_allowed = 0) { return 403; }" in block
        assert "geo-otp-operator-access.log geo_otp_operator" in block
        assert f"limit_req zone={zone}" in block
        assert "proxy_set_header X-Forwarded-Host $http_host;" in block
        assert "proxy_set_header X-Forwarded-Port $server_port;" in block

    assert "location = /api/v2/otp/push" not in locations
    assert "location = /api/v2/otp/smsforwarder.apk" not in locations
    # Scoped app filter preserves non-sensitive diagnostics.
    assert "--no-access-log" not in api_service


def test_public_ip_tls_is_publicly_trusted_and_automatically_renewed() -> None:
    server = (ROOT / "deploy/production/geo-platform-v2.conf").read_text(encoding="utf-8")
    acme = (ROOT / "deploy/production/geo-platform-v2-acme-http.conf").read_text(
        encoding="utf-8"
    )
    deploy_hook = (ROOT / "deploy/production/certbot-reload-nginx").read_text(
        encoding="utf-8"
    )

    assert "ssl_certificate     /etc/letsencrypt/live/39.105.175.14/fullchain.pem;" in server
    assert "ssl_certificate_key /etc/letsencrypt/live/39.105.175.14/privkey.pem;" in server
    assert "geosys-selfsigned" not in server

    assert "listen 80 default_server;" in acme
    assert "listen [::]:80 default_server;" in acme
    assert "server_name 39.105.175.14;" in acme
    assert "location ^~ /.well-known/acme-challenge/" in acme
    assert "root /var/lib/letsencrypt;" in acme
    assert "location / {\n        return 404;\n    }" in acme

    assert "/usr/sbin/nginx -t" in deploy_hook
    assert "/usr/bin/systemctl reload nginx.service" in deploy_hook
