"""W2 抓取层（fetch_run_sources）单元测试。

规划/抽取/分类全部纯函数直测；主流程依赖注入 fake fetcher/sink/loader，
绝不启动真浏览器/DB/MinIO/网络。覆盖：URL 去重与上限、stdlib 密度抽取
（正文/JS 壳/空页）、httpx→浏览器回退触发条件、按域限速、幂等复用、
disabled 开关、INV-32 如实状态落库。
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any

import pytest
from PIL import Image
from temporalio.exceptions import ApplicationError

from workflows.activities.source_fetch import (
    BrandMentionCapture,
    ExistingDocument,
    HttpAttempt,
    PersistedDocument,
    RunSourceContext,
    SourceFetchConfig,
    SourceFetchInput,
    SourceTarget,
    classify_attempt,
    derive_document_pub_id,
    derive_evidence_pub_id,
    execute_source_fetch,
    extract_text_from_html,
    find_brand_term,
    is_static_resource_url,
    looks_like_js_shell,
    normalize_brand_terms,
    plan_source_targets,
    run_source_fetch,
    url_dedupe_key,
)

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_RUN = "run_0123456789abcdef"
_RUN_CREATED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


_ITEM = SourceFetchInput(tenant_pub_id=_TENANT, project_pub_id=_PROJECT, run_pub_id=_RUN)


def _config(**overrides: Any) -> SourceFetchConfig:
    base: dict[str, Any] = {"enabled": True, "limit": 5}
    base.update(overrides)
    return SourceFetchConfig(**base)


def _context(
    *,
    tasks: list[tuple[str, list[dict[str, Any]]]] | None = None,
    existing: dict[str, ExistingDocument] | None = None,
    brand_terms: tuple[str, ...] = (),
) -> RunSourceContext:
    return RunSourceContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        run_id="00000000-0000-0000-0000-000000000003",
        run_pub_id=_RUN,
        project_pub_id=_PROJECT,
        created_at=_RUN_CREATED_AT,
        tasks=tasks or [],
        existing=existing or {},
        brand_terms=brand_terms,
    )


def _ok_attempt(url: str, text: str = "正文 " * 100) -> HttpAttempt:
    return HttpAttempt(
        final_url=url,
        http_status=200,
        text=text,
        extractor="density-extract-v1",
        error_kind=None,
        detail=None,
    )


class _FakeLoader:
    def __init__(self, context: RunSourceContext | None) -> None:
        self._context = context

    def load(
        self, tenant_pub_id: str, run_pub_id: str, project_pub_id: str
    ) -> RunSourceContext | None:
        return self._context


class _FakeFetcher:
    """抓取替身：httpx 结果按 URL 映射，browser 结果按 URL 映射，记录调用顺序。"""

    def __init__(
        self,
        *,
        httpx_results: dict[str, HttpAttempt] | None = None,
        browser_results: dict[str, HttpAttempt] | None = None,
        browser_errors: dict[str, Exception] | None = None,
        brand_captures: dict[str, BrandMentionCapture | None] | None = None,
    ) -> None:
        self._httpx = httpx_results or {}
        self._browser = browser_results or {}
        self._browser_errors = browser_errors or {}
        self._brand_captures = brand_captures or {}
        self.httpx_calls: list[str] = []
        self.browser_calls: list[str] = []
        self.brand_capture_calls: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    def fetch_httpx(self, url: str) -> HttpAttempt:
        self.httpx_calls.append(url)
        return self._httpx[url]

    def fetch_browser(self, url: str) -> HttpAttempt:
        self.browser_calls.append(url)
        if url in self._browser_errors:
            raise self._browser_errors[url]
        return self._browser[url]

    def capture_brand_mention(
        self, url: str, brand_terms: tuple[str, ...]
    ) -> BrandMentionCapture | None:
        self.brand_capture_calls.append((url, brand_terms))
        return self._brand_captures.get(url)

    def close(self) -> None:
        self.closed = True


class _FakeSink:
    def __init__(self) -> None:
        self.persisted: list[dict[str, Any]] = []
        self.linked: list[tuple[str, tuple[str, ...]]] = []

    def persist(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        final_url: str | None,
        http_status: int | None,
        extract_status: str,
        extractor: str | None,
        text: str,
        fetched_at: datetime,
        brand_mention: BrandMentionCapture | None = None,
    ) -> PersistedDocument:
        self.persisted.append(
            {
                "url": target.url,
                "url_hash": target.url_hash,
                "final_url": final_url,
                "http_status": http_status,
                "extract_status": extract_status,
                "extractor": extractor,
                "text": text,
                "fetched_at": fetched_at,
                "brand_mention": brand_mention,
            }
        )
        pub_id = derive_document_pub_id(context.tenant_pub_id, context.run_pub_id, target.url_hash)
        self.link(
            context=context,
            target=target,
            source_document_pub_id=pub_id,
        )
        return PersistedDocument(pub_id=pub_id, bytes=len(text.encode("utf-8")))

    def link(
        self,
        *,
        context: RunSourceContext,
        target: SourceTarget,
        source_document_pub_id: str,
    ) -> None:
        del context
        self.linked.append((source_document_pub_id, target.task_pub_ids))


def _citation(url: str, cited_text: str | None = None) -> dict[str, Any]:
    return {"url": url, "title": "t", "cited_text": cited_text}


# ---------------------------------------------------------------------------
# URL 规划
# ---------------------------------------------------------------------------


def test_url_dedupe_key_normalizes() -> None:
    assert url_dedupe_key("HTTPS://WWW.Example.COM/a/?b=1#frag") == "https://example.com/a?b=1"
    assert url_dedupe_key("http://example.com:80/a") == "http://example.com/a"
    assert url_dedupe_key("https://example.com:443/a/") == "https://example.com/a"
    assert url_dedupe_key("ftp://example.com/a") is None
    assert url_dedupe_key("not-a-url") is None


def test_is_static_resource_url() -> None:
    assert is_static_resource_url("https://example.com/a.png")
    assert is_static_resource_url("https://example.com/a.PDF")
    assert is_static_resource_url("https://example.com/app.js")
    assert not is_static_resource_url("https://example.com/article")
    assert not is_static_resource_url("https://example.com/article?x=1")


def test_plan_source_targets_dedupes_orders_and_caps() -> None:
    tasks = [
        (
            "tsk_1",
            [
                _citation("https://a.example.com/1"),
                _citation("https://b.example.com/2#x"),
                _citation("https://a.example.com/1"),  # 重复 → 丢弃
                _citation("https://static.example.com/x.css"),  # 静态资源 → 丢弃
                _citation("ftp://bad.example.com/x"),  # 非 http → 丢弃
            ],
        ),
        ("tsk_2", [_citation("https://b.example.com/2"), _citation("https://c.example.com/3")]),
    ]
    targets = plan_source_targets(tasks, limit=5)
    assert [t.url for t in targets] == [
        "https://a.example.com/1",
        "https://b.example.com/2#x",
        "https://c.example.com/3",
    ]
    # 去重键忽略 fragment：b.example.com/2 与 b.example.com/2#x 应视为同一条
    assert len({t.key for t in targets}) == 3


def test_plan_source_targets_respects_limit() -> None:
    tasks = [("tsk_1", [_citation(f"https://{i}.example.com/a") for i in range(10)])]
    assert len(plan_source_targets(tasks, limit=5)) == 5
    assert len(plan_source_targets(tasks, limit=1)) == 1


def test_plan_source_targets_applies_limit_per_answer_and_fans_out_shared_url() -> None:
    shared = "https://shared.example.com/article"
    tasks = [
        (
            "ans_1",
            [
                _citation("https://a.example.com/1"),
                _citation(shared),
                _citation("https://a.example.com/3"),
            ],
        ),
        (
            "ans_2",
            [
                _citation(shared),
                _citation("https://b.example.com/2"),
                _citation("https://b.example.com/3"),
            ],
        ),
    ]

    targets = plan_source_targets(tasks, limit=2, run_limit=20)

    assert [target.url for target in targets] == [
        "https://a.example.com/1",
        shared,
        "https://b.example.com/2",
    ]
    shared_target = next(target for target in targets if target.url == shared)
    assert shared_target.task_pub_ids == ("ans_1", "ans_2")


def test_plan_source_targets_prioritizes_verbatim_citations_and_honors_run_cap() -> None:
    tasks = [
        (
            "ans_1",
            [
                _citation("https://plain.example.com/1"),
                _citation("https://quoted.example.com/2", "逐字引文"),
            ],
        ),
        ("ans_2", [_citation("https://second.example.com/1", "另一引文")]),
    ]

    targets = plan_source_targets(tasks, limit=1, run_limit=1)

    assert [target.url for target in targets] == ["https://quoted.example.com/2"]
    assert targets[0].task_pub_ids == ("ans_1",)


def test_plan_source_targets_run_cap_is_round_robin_across_answers() -> None:
    tasks = [
        (
            "ans_1",
            [
                _citation("https://first.example.com/1"),
                _citation("https://first.example.com/2"),
            ],
        ),
        ("ans_2", [_citation("https://second.example.com/1")]),
    ]

    targets = plan_source_targets(tasks, limit=2, run_limit=2)

    assert [target.url for target in targets] == [
        "https://first.example.com/1",
        "https://second.example.com/1",
    ]


# ---------------------------------------------------------------------------
# stdlib 密度抽取
# ---------------------------------------------------------------------------

_ARTICLE_HTML = """
<html><head><title>t</title><style>body{color:red}</style></head>
<body>
<nav><a href="/">首页</a><a href="/about">关于我们</a></nav>
<article>
<h1>中意人寿推出新重疾险</h1>
<p>中意人寿保险有限公司今日宣布推出全新重大疾病保险产品，覆盖一百二十种疾病，
包含轻症豁免保费责任，面向全国销售，旨在提升家庭健康保障水平，满足人民群众
日益增长的健康管理需求，为客户提供全生命周期的风险保障服务。</p>
<p>该公司成立于二零零二年，由中国石油天然气集团与意大利忠利保险合资组建，
注册资本三十七亿元人民币，是国内颇具规模的合资寿险公司之一，业务覆盖全国
多个省市自治区，服务客户数以百万计，长期保持稳健的经营风格与偿付能力。</p>
<script>var x = 1; track(x);</script>
</article>
<footer>版权所有 2026 不得转载</footer>
</body></html>
"""


def test_extract_text_from_html_article() -> None:
    text = extract_text_from_html(_ARTICLE_HTML)
    assert "中意人寿推出新重疾险" in text
    assert "覆盖一百二十种疾病" in text
    assert "注册资本三十七亿元" in text
    # chrome 内容被丢弃
    assert "track(x)" not in text
    assert "color:red" not in text
    assert len(text) >= 200


def test_extract_text_from_html_js_shell_is_empty() -> None:
    shell = (
        '<html><body><div id="app"></div>'
        '<script src="/static/app.js">var boot = "' + "x" * 3000 + '";</script></body></html>'
    )
    text = extract_text_from_html(shell)
    assert looks_like_js_shell(shell, text)
    assert len(text) < 200


def test_extract_text_from_html_empty_page() -> None:
    assert extract_text_from_html("") == ""
    assert extract_text_from_html("<html><body></body></html>") == ""


def test_extract_text_truncates_at_limit() -> None:
    html = f"<html><body><p>{'长' * 30000}</p></body></html>"
    assert len(extract_text_from_html(html)) == 20_000


def test_looks_like_js_shell_short_text_long_html() -> None:
    assert looks_like_js_shell("x" * 3000, "短")
    assert not looks_like_js_shell("x" * 3000, "正文 " * 100)


def test_brand_terms_are_exact_stable_and_drop_unsafe_one_character_aliases() -> None:
    terms = normalize_brand_terms([" 盛邦安全 ", "SBAQ", "盛邦安全", "安", None])
    assert terms == ("盛邦安全", "SBAQ")
    assert find_brand_term("本文比较盛邦安全与其他厂商。", terms) == "盛邦安全"
    assert find_brand_term("本文没有目标品牌。", terms) is None


# ---------------------------------------------------------------------------
# 抓取结果分类（回退触发条件）
# ---------------------------------------------------------------------------


def test_classify_attempt_ok() -> None:
    assert classify_attempt(_ok_attempt("https://a.example.com/")) == ("ok", False)


def test_classify_attempt_short_text_falls_back() -> None:
    attempt = HttpAttempt("https://a.example.com/", 200, "短", "density-extract-v1", None, None)
    assert classify_attempt(attempt) == ("extract_empty", True)


def test_classify_attempt_blocked_statuses_fall_back() -> None:
    for status in (401, 403, 429):
        attempt = HttpAttempt("https://a.example.com/", status, "", None, None, None)
        assert classify_attempt(attempt) == ("blocked", True)


def test_classify_attempt_other_http_errors_no_fallback() -> None:
    for status in (404, 500):
        attempt = HttpAttempt("https://a.example.com/", status, "", None, None, None)
        assert classify_attempt(attempt) == ("http_error", False)


def test_classify_attempt_timeout_and_transport_fallback() -> None:
    timeout = HttpAttempt(None, None, "", None, "timeout", "ReadTimeout")
    assert classify_attempt(timeout) == ("timeout", True)
    transport = HttpAttempt(None, None, "", None, "transport", "ConnectError")
    assert classify_attempt(transport) == ("http_error", True)


# ---------------------------------------------------------------------------
# pub_id 派生确定性
# ---------------------------------------------------------------------------


def test_derived_pub_ids_are_deterministic() -> None:
    a = derive_document_pub_id(_TENANT, _RUN, "h" * 64)
    b = derive_document_pub_id(_TENANT, _RUN, "h" * 64)
    assert a == b and a.startswith("srd_") and len(a) == 30
    e1 = derive_evidence_pub_id(_TENANT, _RUN, "https://a.example.com/1")
    e2 = derive_evidence_pub_id(_TENANT, _RUN, "https://a.example.com/1")
    assert e1 == e2 and e1.startswith("evd_") and len(e1) == 30


# ---------------------------------------------------------------------------
# 主流程（fake 注入）
# ---------------------------------------------------------------------------


def test_execute_fetch_disabled_skips_zero_io() -> None:
    loader = _FakeLoader(None)
    fetcher = _FakeFetcher()
    sink = _FakeSink()
    result = execute_source_fetch(
        _ITEM, config=_config(enabled=False), loader=loader, fetcher=fetcher, sink=sink
    )
    assert result.skipped == "disabled"
    assert result.fetched == [] and sink.persisted == []


def test_execute_fetch_run_not_found_raises() -> None:
    with pytest.raises(ApplicationError, match="run not found"):
        execute_source_fetch(
            _ITEM,
            config=_config(),
            loader=_FakeLoader(None),
            fetcher=_FakeFetcher(),
            sink=_FakeSink(),
        )


def test_execute_fetch_happy_path_httpx_only() -> None:
    url = "https://a.example.com/article"
    tasks = [("tsk_1", [_citation(url, "引述")])]
    fetcher = _FakeFetcher(httpx_results={url: _ok_attempt(url)})
    sink = _FakeSink()
    sleeps: list[float] = []
    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=fetcher,
        sink=sink,
        sleep=sleeps.append,
    )
    assert result.skipped is None and result.failures == []
    assert len(result.fetched) == 1
    entry = result.fetched[0]
    assert entry.url == url and entry.extract_status == "ok"
    assert entry.source_document_pub_id.startswith("srd_") and entry.bytes > 0
    assert entry.answer_pub_ids == ("tsk_1",)
    assert fetcher.browser_calls == []  # 正文达标不触发浏览器回退
    assert sink.persisted[0]["extract_status"] == "ok"
    assert sink.persisted[0]["extractor"] == "density-extract-v1"
    assert sink.linked == [(entry.source_document_pub_id, ("tsk_1",))]
    assert len(sleeps) == 0  # 同域首次请求不限速


def test_execute_fetch_only_persists_dom_verified_brand_mention() -> None:
    url = "https://a.example.com/article"
    text = "盛邦安全提供攻击面管理能力。" + ("正文 " * 100)
    capture = BrandMentionCapture(
        png_bytes=_png_bytes(240, 80),
        matched_text="盛邦安全",
        paragraph_text="盛邦安全提供攻击面管理能力。",
        text_start=0,
        text_end=4,
        bbox={"x": 2.0, "y": 3.0, "width": 60.0, "height": 24.0, "confidence": 1.0},
    )
    fetcher = _FakeFetcher(
        httpx_results={url: _ok_attempt(url, text)}, brand_captures={url: capture}
    )
    sink = _FakeSink()

    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(
            _context(tasks=[("ans_1", [_citation(url)])], brand_terms=("盛邦安全", "SBAQ"))
        ),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )

    assert result.failures == []
    assert result.fetched[0].brand_mention_captured is True
    assert fetcher.brand_capture_calls == [(url, ("盛邦安全", "SBAQ"))]
    persisted_capture = sink.persisted[0]["brand_mention"]
    assert isinstance(persisted_capture, BrandMentionCapture)
    assert persisted_capture.png_bytes == capture.png_bytes
    assert persisted_capture.bbox == {
        "x": 2.0,
        "y": 3.0,
        "width": 60.0,
        "height": 24.0,
        "confidence": 1.0,
        "image_width": 240.0,
        "image_height": 80.0,
    }


def test_execute_fetch_rejects_bbox_outside_decoded_png() -> None:
    url = "https://a.example.com/article"
    text = "盛邦安全提供攻击面管理能力。" + ("正文 " * 100)
    capture = BrandMentionCapture(
        png_bytes=_png_bytes(1, 1),
        matched_text="盛邦安全",
        paragraph_text="盛邦安全提供攻击面管理能力。",
        text_start=0,
        text_end=4,
        bbox={"x": 108.0, "y": 0.0, "width": 58.0, "height": 20.0},
    )
    fetcher = _FakeFetcher(
        httpx_results={url: _ok_attempt(url, text)}, brand_captures={url: capture}
    )
    sink = _FakeSink()

    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(
            _context(tasks=[("ans_1", [_citation(url)])], brand_terms=("盛邦安全",))
        ),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )

    assert result.fetched[0].brand_mention_captured is False
    assert sink.persisted[0]["brand_mention"] is None
    assert [failure.error for failure in result.failures] == ["brand_capture: invalid_png_or_bbox"]


def test_execute_fetch_rejects_screenshot_when_live_dom_does_not_match_brand() -> None:
    url = "https://a.example.com/article"
    text = "盛邦安全提供攻击面管理能力。" + ("正文 " * 100)
    fetcher = _FakeFetcher(httpx_results={url: _ok_attempt(url, text)}, brand_captures={url: None})
    sink = _FakeSink()

    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(
            _context(tasks=[("ans_1", [_citation(url)])], brand_terms=("盛邦安全",))
        ),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )

    assert result.fetched[0].brand_mention_captured is False
    assert sink.persisted[0]["brand_mention"] is None
    assert [failure.error for failure in result.failures] == [
        "brand_capture: dom_brand_term_not_found"
    ]


def test_execute_fetch_browser_fallback_on_js_shell() -> None:
    url = "https://spa.example.com/app"
    tasks = [("tsk_1", [_citation(url)])]
    shell_attempt = HttpAttempt(url, 200, "", None, None, None)
    browser_attempt = HttpAttempt(url, None, "正文 " * 100, "innertext-v1", None, None)
    fetcher = _FakeFetcher(
        httpx_results={url: shell_attempt}, browser_results={url: browser_attempt}
    )
    sink = _FakeSink()
    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )
    assert fetcher.browser_calls == [url]
    assert result.fetched[0].extract_status == "ok"
    assert sink.persisted[0]["extractor"] == "innertext-v1"


def test_execute_fetch_browser_fallback_failure_keeps_honest_status() -> None:
    url = "https://blocked.example.com/a"
    tasks = [("tsk_1", [_citation(url)])]
    attempt = HttpAttempt(url, 403, "", None, None, None)
    fetcher = _FakeFetcher(
        httpx_results={url: attempt}, browser_errors={url: RuntimeError("goto failed")}
    )
    sink = _FakeSink()
    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )
    assert result.fetched[0].extract_status == "blocked"  # INV-32：如实，不编造正文
    assert sink.persisted[0]["text"] == ""
    assert result.fetched[0].bytes == 0


def test_execute_fetch_http_error_records_status() -> None:
    url = "https://dead.example.com/404"
    tasks = [("tsk_1", [_citation(url)])]
    attempt = HttpAttempt(url, 404, "", None, None, None)
    fetcher = _FakeFetcher(httpx_results={url: attempt})
    sink = _FakeSink()
    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )
    assert result.fetched[0].extract_status == "http_error"
    assert fetcher.browser_calls == []  # 404 不触发回退


def test_execute_fetch_per_domain_rate_limit() -> None:
    url1 = "https://same.example.com/1"
    url2 = "https://same.example.com/2"
    url3 = "https://other.example.com/3"
    tasks = [("tsk_1", [_citation(url1), _citation(url2), _citation(url3)])]
    fetcher = _FakeFetcher(
        httpx_results={u: _ok_attempt(u) for u in (url1, url2, url3)},
    )
    sleeps: list[float] = []
    now = [1000.0]

    def _monotonic() -> float:
        return now[0]

    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=fetcher,
        sink=_FakeSink(),
        sleep=sleeps.append,
        monotonic=_monotonic,
    )
    assert len(result.fetched) == 3
    # 同域第二次请求触发一次 ~2s 等待；异域首次不等待
    assert len(sleeps) == 1
    assert 1.9 < sleeps[0] <= 2.0


def test_execute_fetch_idempotent_reuse_existing() -> None:
    url = "https://a.example.com/article"
    key = url_dedupe_key(url)
    assert key is not None
    url_hash = sha256(key.encode()).hexdigest()
    existing = {url_hash: ExistingDocument(pub_id="srd_existing", extract_status="ok", bytes=1234)}
    tasks = [("tsk_1", [_citation(url)])]
    fetcher = _FakeFetcher()  # 无任何映射：被调用即 KeyError
    sink = _FakeSink()
    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks, existing=existing)),
        fetcher=fetcher,
        sink=sink,
        sleep=lambda _s: None,
    )
    # 重跑：不重复抓取、不重复 capture，直接复用既存行
    assert fetcher.httpx_calls == [] and fetcher.browser_calls == []
    assert sink.persisted == []
    assert result.fetched[0].source_document_pub_id == "srd_existing"
    assert result.fetched[0].extract_status == "ok"
    assert result.fetched[0].bytes == 1234
    assert sink.linked == [("srd_existing", ("tsk_1",))]


def test_execute_fetch_persist_failure_goes_to_failures() -> None:
    url = "https://a.example.com/article"
    tasks = [("tsk_1", [_citation(url)])]

    class _BoomSink(_FakeSink):
        def persist(self, **kwargs: Any) -> PersistedDocument:
            raise RuntimeError("db down")

    result = execute_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        fetcher=_FakeFetcher(httpx_results={url: _ok_attempt(url)}),
        sink=_BoomSink(),
        sleep=lambda _s: None,
    )
    assert result.fetched == []
    assert len(result.failures) == 1 and "db down" in result.failures[0].error


@pytest.mark.asyncio
async def test_run_source_fetch_injected_fetcher_runs_inline() -> None:
    url = "https://a.example.com/article"
    tasks = [("tsk_1", [_citation(url)])]
    fetcher = _FakeFetcher(httpx_results={url: _ok_attempt(url)})
    beats: list[dict[str, Any]] = []
    result = await run_source_fetch(
        _ITEM,
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        sink=_FakeSink(),
        fetcher_factory=lambda _cfg: fetcher,
        heartbeat=beats.append,
        sleep=lambda _s: None,
    )
    assert result.skipped is None
    assert len(result.fetched) == 1
    assert beats and fetcher.closed
