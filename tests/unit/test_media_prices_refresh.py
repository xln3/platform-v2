import os
import stat

from geo_platform.config import get_settings
from geo_platform.posting.provider_credentials import ProviderCredentialStore

from tools import media_prices_refresh as refresh


def test_dataset_directory_follows_api_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))

    assert refresh._configured_datasets_dir() == tmp_path

    monkeypatch.delenv("GEO_DATASETS_DIR")

    assert refresh._configured_datasets_dir() == refresh.ROOT / ".datasets"

    monkeypatch.setenv("GEO_DATASETS_DIR", "")

    assert refresh._configured_datasets_dir() == refresh.ROOT / ".datasets"


def test_refreshed_prfabu_session_is_replaced_with_owner_only_permissions(
    tmp_path, monkeypatch
) -> None:
    session_file = tmp_path / "prfabu_session.txt"
    session_file.write_text("old-session", encoding="utf-8")
    session_file.chmod(0o644)
    monkeypatch.setattr(refresh, "SESSION_FILE", session_file)
    cookies = refresh.httpx.Cookies()
    cookies.set("PHPSESSID", "refreshed-session", domain="www.prfabu.com")

    refresh._save_phpsessid(cookies)

    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    assert "refreshed-session" in session_file.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".prfabu_session.txt.*.tmp")) == []


def test_web_refresh_uses_and_rotates_requesting_tenant_encrypted_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("GEO_ENV", "test")
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_KMS_MASTER_KEY", "refresh-provider-session-test-key")
    monkeypatch.setattr(refresh, "CREDENTIAL_TENANT_ID", "tnt_refresh_owner")
    get_settings.cache_clear()
    store = ProviderCredentialStore()
    store.save_credentials(
        tenant_pub_id="tnt_refresh_owner",
        provider="prfabu",
        account="supplier-account",
        password="supplier-password",
    )
    store.update_session(
        tenant_pub_id="tnt_refresh_owner",
        provider="prfabu",
        cookies={"PHPSESSID": "encrypted-old-session"},
        status="ready",
        message="会话有效",
    )
    legacy = tmp_path / "legacy-session.txt"
    legacy.write_text(
        "# Netscape HTTP Cookie File\n"
        "www.prfabu.com\tFALSE\t/\tFALSE\t0\tPHPSESSID\tlegacy-must-not-win\n",
        encoding="utf-8",
    )

    assert refresh._provider_session_cookies("prfabu", legacy) == {
        "PHPSESSID": "encrypted-old-session"
    }
    with refresh.httpx.Client() as client:
        client.cookies.set("PHPSESSID", "encrypted-rotated-session", domain="www.prfabu.com")
        refresh._persist_provider_session("prfabu", client)

    account = store.load(tenant_pub_id="tnt_refresh_owner", provider="prfabu")
    assert account.cookies == {"PHPSESSID": "encrypted-rotated-session"}
    encrypted_record = (
        tmp_path / ".provider-credentials" / "tnt_refresh_owner" / "prfabu.json"
    ).read_bytes()
    assert b"encrypted-rotated-session" not in encrypted_record
    get_settings.cache_clear()


def test_pinda_news_row_maps_to_common_price_schema() -> None:
    row = refresh._normalize_pinda_row(
        {
            "id": "897670",
            "name": "凤凰网客户端",
            "pay_price": 42,
            "resourceType": "新闻资讯",
            "area": "综合全国",
            "speed": "12",
            "publishRate": "90",
            "newsSource": "网页",
            "pcWeight": "5",
            "mobileWeight": "6",
            "brief": "出稿快",
            "exampleUrl": "https://example.test/case",
        }
    )

    # Preserve the provider's stable media identifier for deduplication and
    # posting attribution; it is not a browser credential or tenant identifier.
    assert row["id"] == "897670"
    assert row["media_name"] == "凤凰网客户端"
    assert row["custom_cost"] == 42
    assert row["channel_type_str"] == "新闻资讯"
    assert row["publication_time_str"] == "12小时"
    assert row["publish_rate"] == 90
    assert row["pc_weight"] == 5
    assert row["m_weight"] == 6
    assert row["case_link"] == "https://example.test/case"


def test_pinda_wemedia_row_keeps_account_platform_in_merge_identity() -> None:
    normalized = refresh._normalize_wemedia_rows(
        "pinda",
        [
            {
                "name": "同名账号",
                "platform": "百家号",
                "classify": "科技",
                "pay_price": 12,
                "realfans": 99_000,
                "totalReadingVolume": 8_000,
                "varifyinfo": "蓝V业务账号",
            },
            {
                "name": "同名账号",
                "platform": "今日头条",
                "classify": "科技",
                "pay_price": 15,
            },
        ],
    )
    payload = refresh._merge_wemedia(
        {"pinda": [{"code": 0, "count": 2, "data": normalized}]},
        {"pinda": "ok"},
    )

    pinda_rows = [
        row for row in payload["rows"] if row["name"] == "同名账号" and row["prices"].get("pinda")
    ]
    assert {(row["platform"], row["best"]) for row in pinda_rows} == {
        ("百家号", 12.0),
        ("今日头条", 15.0),
    }
    assert next(row for row in pinda_rows if row["platform"] == "百家号")["fans"] == "99000"


def test_refresh_summary_reports_news_and_wemedia_status_separately() -> None:
    sources = {
        platform: {
            "status": "ok",
            "rows": 1,
            "note": "",
            "wemedia_status": "ok",
            "wemedia_rows": 2,
            "wemedia_note": "",
        }
        for platform in refresh.PLATS
    }
    sources["pinda"]["wemedia_status"] = "partial"
    sources["pinda"]["wemedia_note"] = "missing_pages:1"

    assert "品达发稿 新闻1/自媒体2(部分采集)" in refresh._summary_message(sources)


def test_pinda_cache_must_match_current_pagination() -> None:
    current = {
        "code": 0,
        "count": 2_004,
        "pages": 3,
        "data": [{"id": index} for index in range(1_000)],
    }
    assert refresh._valid_pinda_cached_page(
        current,
        page=2,
        total_pages=3,
        total_rows=2_004,
    )
    assert not refresh._valid_pinda_cached_page(
        {**current, "pages": 11, "data": current["data"][:200]},
        page=2,
        total_pages=3,
        total_rows=2_004,
    )
    assert refresh._valid_pinda_cached_page(
        {**current, "data": current["data"][:4]},
        page=3,
        total_pages=3,
        total_rows=2_004,
    )


def test_browser_required_labels_match_client_secret_boundary() -> None:
    assert refresh._browser_safe_required_label("正常媒体名称", 500) == "正常媒体名称"
    assert refresh._browser_safe_required_label("173183", 500) is None
    assert refresh._browser_safe_required_label("中国青年报（200-300字）", 500) is None
    assert refresh._browser_safe_required_label("账号202210", 500) is None
    assert refresh._browser_safe_required_label("联系电话13800138000", 500) is None


def test_refresh_lock_does_not_expire_while_owner_is_alive(tmp_path, monkeypatch) -> None:
    lock_file = tmp_path / "media-prices.refresh.lock"
    lock_file.write_text(f"pid={os.getpid()} started=old\n", encoding="utf-8")
    os.utime(lock_file, (1, 1))
    monkeypatch.setattr(refresh, "LOCK_FILE", lock_file)

    assert refresh._acquire_lock() is False
    assert lock_file.read_text(encoding="utf-8").startswith(f"pid={os.getpid()}")


def test_refresh_lock_immediately_recovers_dead_owner(tmp_path, monkeypatch) -> None:
    lock_file = tmp_path / "media-prices.refresh.lock"
    lock_file.write_text("pid=999999999 started=recent\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "LOCK_FILE", lock_file)

    assert refresh._acquire_lock() is True
    assert lock_file.read_text(encoding="utf-8").startswith(f"pid={os.getpid()}")
