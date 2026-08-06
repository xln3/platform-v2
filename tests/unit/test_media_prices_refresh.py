import stat

from tools import media_prices_refresh as refresh


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
