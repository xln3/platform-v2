from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_feishu_bot_unit_uses_credentials_loopback_and_no_proxy() -> None:
    unit = (ROOT / "deploy/production/geo-platform-v2-feishu-bot.service").read_text()
    assert "GEO_FEISHU_BOT_ADDRESS=127.0.0.1" not in unit  # supplied by non-secret env file
    assert "EnvironmentFile=/etc/geo-platform-v2/feishu-bot.env" in unit
    assert "geo_platform.notifications.bot" in unit
    for key in (
        "feishu-app-secret",
        "feishu-verification-token",
        "feishu-encrypt-key",
        "feishu-allowed-open-ids",
        "feishu-link-signing-key",
    ):
        assert f"LoadCredential={key}:" in unit
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert f'Environment="{key}="' in unit
    for key in ("NO_PROXY", "no_proxy"):
        assert f'Environment="{key}=*"' in unit
    assert "APP_SECRET=" not in unit
    assert "VERIFICATION_TOKEN=" not in unit
    assert "ENCRYPT_KEY=" not in unit


def test_nginx_exposes_one_hardened_exact_callback_location() -> None:
    server = (ROOT / "deploy/production/geo-platform-v2.conf").read_text()
    locations = (ROOT / "deploy/production/nginx-v2-locations.conf").read_text()
    callback = "location = /api/v2/integrations/feishu/card-action"
    assert locations.count(callback) == 1
    assert "limit_req_zone" in server and "geo_feishu_callback" in server
    assert "access_log off" in locations
    assert "client_max_body_size 256k" in locations
    assert "limit_req zone=geo_feishu_callback" in locations
    assert "proxy_pass http://127.0.0.1:18092/callbacks/feishu/card-action" in locations
    assert "proxy_read_timeout 3s" in locations


def test_notification_migration_never_persists_raw_ticket_or_token() -> None:
    migration = (ROOT / "migrations/versions/s06_0025_feishu_app_notifications.py").read_text()
    assert "assist_ticket_sha256" in migration
    assert "tenant_access_token" not in migration
    assert "app_secret" not in migration.lower()
    assert "raw_ticket" not in migration.lower()
