"""W4 官网素材采集 activity 单元测试。

规划层全部纯函数直测；抓取+存证主流程依赖注入 fake fetcher/sink/loader，
绝不启动真浏览器/DB/MinIO。覆盖：归一化命中、去重、上限、交集优先级、
单页失败不中断、pub_id/capture_time 确定性、disabled/no_website 开关。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.own_site_snapshot import (
    FetchedPage,
    OwnSiteSnapshotConfig,
    OwnSiteSnapshotInput,
    PersistedPage,
    RunSnapshotContext,
    SnapshotTarget,
    build_text_payload,
    clean_text,
    derive_evidence_pub_id,
    execute_own_site_capture,
    homepage_url,
    host_matches_domain,
    merge_targets,
    normalize_host,
    plan_citation_targets,
    relation_for_target,
    run_own_site_snapshots,
    select_site_targets,
    url_dedupe_key,
)

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_RUN = "run_0123456789abcdef"
_RUN_CREATED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_FETCHED_AT = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)


def _config(**overrides: Any) -> OwnSiteSnapshotConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "snapshot_limit": 5,
        "citation_limit": 5,
        "proxy_url": None,
    }
    base.update(overrides)
    return OwnSiteSnapshotConfig(**base)


def _context(
    *,
    website: str | None = "https://www.example.com",
    tasks: list[tuple[str, list[dict[str, Any]]]] | None = None,
) -> RunSnapshotContext:
    return RunSnapshotContext(
        run_pub_id=_RUN,
        project_pub_id=_PROJECT,
        created_at=_RUN_CREATED_AT,
        website=website,
        tasks=tasks or [],
    )


def _page(url: str, *, text: str = "正文内容", links: list[str] | None = None) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        title=f"{url} 标题",
        text=text,
        links=links or [],
        png_bytes=b"\x89PNG-fake-" + url.encode(),
        fetched_at=_FETCHED_AT,
    )


class _FakeLoader:
    def __init__(self, context: RunSnapshotContext | None) -> None:
        self._context = context
        self.calls = 0

    def load(
        self, tenant_pub_id: str, run_pub_id: str, project_pub_id: str
    ) -> RunSnapshotContext | None:
        self.calls += 1
        return self._context


class _FakeSession:
    """注入的抓取层替身：pages 映射 URL→FetchedPage，errors 映射 URL→异常。"""

    def __init__(
        self,
        *,
        pages: dict[str, FetchedPage] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._pages = pages or {}
        self._errors = errors or {}
        self.fetched: list[str] = []
        self.closed = False

    def fetch(self, url: str) -> FetchedPage:
        self.fetched.append(url)
        if url in self._errors:
            raise self._errors[url]
        return self._pages[url]

    def close(self) -> None:
        self.closed = True


def _factory(session: _FakeSession) -> Callable[[OwnSiteSnapshotConfig], _FakeSession]:
    def _make(config: OwnSiteSnapshotConfig) -> _FakeSession:
        return session

    return _make


class _FakeSink:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_on = fail_on or set()

    def persist_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        run_pub_id: str,
        target: SnapshotTarget,
        fetched: FetchedPage,
        from_pub_id: str,
        relation_type: str,
        capture_time: datetime,
    ) -> PersistedPage:
        self.calls.append(
            {
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "run_pub_id": run_pub_id,
                "url": target.url,
                "kind": target.kind,
                "from_pub_id": from_pub_id,
                "relation_type": relation_type,
                "capture_time": capture_time,
            }
        )
        if target.url in self._fail_on:
            raise RuntimeError("sink boom")
        return PersistedPage(
            evidence_pub_id=derive_evidence_pub_id(
                tenant_pub_id, run_pub_id, target.url, "own_site_snapshot", "text"
            ),
            png_evidence_pub_id=derive_evidence_pub_id(
                tenant_pub_id, run_pub_id, target.url, "own_site_snapshot", "png"
            ),
            byte_size=len(fetched.text.encode("utf-8")) + len(fetched.png_bytes),
        )


def _item() -> OwnSiteSnapshotInput:
    return OwnSiteSnapshotInput(tenant_pub_id=_TENANT, project_pub_id=_PROJECT, run_pub_id=_RUN)


# ---------------------------------------------------------------------------
# 纯函数：host / URL 归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.example.com/", "example.com"),
        ("https://Example.COM/path", "example.com"),
        ("example.com", "example.com"),
        ("http://example.com:8080/x", "example.com"),
        ("www.example.com", "example.com"),
        ("example.com.", "example.com"),
        ("https://news.example.com/a", "news.example.com"),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_host(value: str, expected: str | None) -> None:
    assert normalize_host(value) == expected


@pytest.mark.parametrize(
    ("host", "domain", "expected"),
    [
        ("example.com", "example.com", True),
        ("news.example.com", "example.com", True),
        ("fakeexample.com", "example.com", False),
        ("example.com.evil.com", "example.com", False),
        ("other.com", "example.com", False),
    ],
)
def test_host_matches_domain(host: str, domain: str, expected: bool) -> None:
    assert host_matches_domain(host, domain) is expected


def test_url_dedupe_key_normalizes() -> None:
    assert url_dedupe_key("HTTPS://WWW.Example.COM:443/about/#team") == "https://example.com/about"
    assert url_dedupe_key("http://example.com:80/a") == "http://example.com/a"
    assert url_dedupe_key("http://example.com:8080/a") == "http://example.com:8080/a"
    assert url_dedupe_key("https://example.com") == "https://example.com/"
    assert url_dedupe_key("https://example.com/a?x=1") == "https://example.com/a?x=1"


def test_url_dedupe_key_rejects_non_http() -> None:
    assert url_dedupe_key("mailto:sales@example.com") is None
    assert url_dedupe_key("tel:+8613800000000") is None
    assert url_dedupe_key("#anchor") is None
    assert url_dedupe_key("javascript:void(0)") is None


def test_homepage_url() -> None:
    assert homepage_url("example.com") == "https://example.com"
    assert homepage_url(" https://www.example.com/ ") == "https://www.example.com/"


# ---------------------------------------------------------------------------
# 纯函数：目标规划
# ---------------------------------------------------------------------------


def test_plan_citation_targets_hit_miss_dedupe_limit() -> None:
    tasks = [
        (
            "ans_aaa",
            [
                {"url": "https://www.example.com/about", "title": "关于", "cited_text": None},
                {"url": "https://external.com/x"},
            ],
        ),
        (
            "ans_bbb",
            [
                {"url": "https://example.com/about#frag"},  # 与 ans_aaa 重复 → 保留首次
                {"url": "https://news.example.com/post"},  # 子域命中
                {"title": "缺 url"},  # 无 url 跳过
                {"url": "http://example.com:8080/ports"},  # 端口归一化命中
            ],
        ),
    ]
    targets = plan_citation_targets(tasks, "example.com", 5)
    assert [(t.url, t.task_pub_id) for t in targets] == [
        ("https://www.example.com/about", "ans_aaa"),
        ("https://news.example.com/post", "ans_bbb"),
        ("http://example.com:8080/ports", "ans_bbb"),
    ]
    assert all(t.kind == "citation" for t in targets)
    # 上限截断且保序
    capped = plan_citation_targets(tasks, "example.com", 2)
    assert [t.url for t in capped] == [
        "https://www.example.com/about",
        "https://news.example.com/post",
    ]


def test_select_site_targets_filters_and_limit() -> None:
    links = [
        "https://example.com/products",
        "https://example.com/logo.png",  # 静态资源
        "https://example.com/guide.pdf",  # 静态资源
        "mailto:sales@example.com",
        "tel:+8613800000000",
        "#top",  # 锚点
        "https://other.com/x",  # 外站
        "https://news.example.com/sub",  # 子域不算同 host（发现策略从紧）
        "https://example.com/products#detail",  # 与首条去重
        "https://example.com/about",
        "https://example.com/contact",
    ]
    targets = select_site_targets(links, "example.com", 2)
    assert [t.url for t in targets] == [
        "https://example.com/products",
        "https://example.com/about",
    ]
    assert all(t.kind == "site_page" for t in targets)
    # exclude（主页自身）+ limit<=0
    excluded = select_site_targets(
        links, "example.com", 5, exclude=frozenset({"https://example.com/products"})
    )
    assert "https://example.com/products" not in [t.url for t in excluded]
    assert select_site_targets(links, "example.com", 0) == []


def test_merge_targets_citation_priority() -> None:
    citation = SnapshotTarget(
        url="https://example.com/about",
        key="https://example.com/about",
        kind="citation",
        task_pub_id="ans_aaa",
    )
    site_same = SnapshotTarget(
        url="https://example.com/about", key="https://example.com/about", kind="site_page"
    )
    site_other = SnapshotTarget(
        url="https://example.com/products", key="https://example.com/products", kind="site_page"
    )
    merged = merge_targets([citation], [site_same, site_other])
    assert merged == [citation, site_other]


def test_relation_for_target() -> None:
    citation = SnapshotTarget(
        url="https://example.com/a",
        key="https://example.com/a",
        kind="citation",
        task_pub_id="ans_aaa",
    )
    assert relation_for_target(citation, _RUN) == ("ans_aaa", "own_site_snapshot")
    site = SnapshotTarget(url="https://example.com", key="https://example.com/", kind="site_page")
    assert relation_for_target(site, _RUN) == (_RUN, "own_site_page")
    with pytest.raises(ValueError, match="task_pub_id"):
        relation_for_target(
            SnapshotTarget(url="https://example.com/a", key="k", kind="citation"), _RUN
        )


def test_derive_evidence_pub_id_deterministic() -> None:
    first = derive_evidence_pub_id(
        _TENANT, _RUN, "https://example.com/a", "own_site_snapshot", "text"
    )
    second = derive_evidence_pub_id(
        _TENANT, _RUN, "https://example.com/a", "own_site_snapshot", "text"
    )
    assert first == second
    assert first.startswith("evd_") and len(first) == 30
    png = derive_evidence_pub_id(_TENANT, _RUN, "https://example.com/a", "own_site_snapshot", "png")
    assert png != first
    other_url = derive_evidence_pub_id(
        _TENANT, _RUN, "https://example.com/b", "own_site_snapshot", "text"
    )
    assert other_url != first


def test_clean_text() -> None:
    raw = "  第一行  有多余   空白\n\n\n\n第二行\t缩进  \n\n\n\n\n第三行"
    assert clean_text(raw) == "第一行 有多余 空白\n\n第二行 缩进\n\n第三行"
    long_text = "x" * 25_000
    assert len(clean_text(long_text)) == 20_000


def test_build_text_payload() -> None:
    page = _page("https://example.com/a", text="官网正文")
    payload = json.loads(build_text_payload("https://example.com/a", page))
    assert payload == {
        "url": "https://example.com/a",
        "final_url": "https://example.com/a",
        "title": "https://example.com/a 标题",
        "fetched_at": _FETCHED_AT.isoformat(),
        "text": "官网正文",
        "text_bytes": len("官网正文".encode()),
        "extractor": "innertext-v1",
    }


def test_config_from_env_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GEO_OWN_SITE_SNAPSHOT_ENABLED",
        "GEO_OWN_SITE_SNAPSHOT_LIMIT",
        "GEO_OWN_SITE_CITATION_LIMIT",
        "GEO_OWN_SITE_PROXY_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    config = OwnSiteSnapshotConfig.from_env()
    assert config.enabled is True
    assert config.snapshot_limit == 5
    assert config.citation_limit == 5
    assert config.proxy_url is None

    monkeypatch.setenv("GEO_OWN_SITE_SNAPSHOT_ENABLED", "false")
    monkeypatch.setenv("GEO_OWN_SITE_SNAPSHOT_LIMIT", "99")  # 硬上限 20
    monkeypatch.setenv("GEO_OWN_SITE_CITATION_LIMIT", "abc")  # 非法 → 默认
    monkeypatch.setenv("GEO_OWN_SITE_PROXY_URL", "http://127.0.0.1:7890")
    config = OwnSiteSnapshotConfig.from_env()
    assert config.enabled is False
    assert config.snapshot_limit == 20
    assert config.citation_limit == 5
    assert config.proxy_url == "http://127.0.0.1:7890"

    monkeypatch.setenv("GEO_OWN_SITE_SNAPSHOT_LIMIT", "0")  # 下限 1
    assert OwnSiteSnapshotConfig.from_env().snapshot_limit == 1


# ---------------------------------------------------------------------------
# 主流程：fake fetcher / sink / loader
# ---------------------------------------------------------------------------

_HOMEPAGE = "https://www.example.com"
_ABOUT = "https://example.com/about"
_NEWS_POST = "https://news.example.com/post"
_PRODUCTS = "https://example.com/products"

_TASKS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "ans_aaa",
        [
            {"url": _ABOUT, "title": "关于我们", "cited_text": None},
            {"url": "https://external.com/x"},
        ],
    ),
    (
        "ans_bbb",
        [
            {"url": f"{_ABOUT}#frag"},
            {"url": _NEWS_POST},
        ],
    ),
]

_HOMEPAGE_LINKS = [
    _PRODUCTS,
    _ABOUT,  # 与引用页重叠 → merge 后只按引用页抓一次
    "https://example.com/logo.png",
    "mailto:sales@example.com",
    "https://other.com/x",
    f"{_PRODUCTS}#top",
]


def _main_flow_pages() -> dict[str, FetchedPage]:
    return {
        _HOMEPAGE: _page(_HOMEPAGE, links=_HOMEPAGE_LINKS),
        _ABOUT: _page(_ABOUT),
        _PRODUCTS: _page(_PRODUCTS),
    }


def test_main_flow_capture_and_relations() -> None:
    session = _FakeSession(
        pages=_main_flow_pages(),
        errors={_NEWS_POST: TimeoutError("boom")},
    )
    sink = _FakeSink()
    sleeps: list[float] = []
    result = execute_own_site_capture(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(tasks=list(_TASKS))),
        session_factory=_factory(session),
        sink=sink,
        sleep=sleeps.append,
    )
    # 主页先抓（发现链接），然后 citation 优先、site_page 殿后
    assert session.fetched == [_HOMEPAGE, _ABOUT, _NEWS_POST, _PRODUCTS]
    assert sleeps == [2.0, 2.0, 2.0]
    assert session.closed is True
    assert result.skipped is None
    # 单页失败进 failures 不中断
    assert [(f.url) for f in result.failures] == [_NEWS_POST]
    assert "TimeoutError" in result.failures[0].error
    # 每页一条 captured；kind 与 relation 词表一致
    assert [(c.url, c.kind) for c in result.captured] == [
        (_HOMEPAGE, "own_site_page"),
        (_ABOUT, "own_site_snapshot"),
        (_PRODUCTS, "own_site_page"),
    ]
    # relation 归属：引用页挂 task pub，官网页挂 run pub
    assert [(c["url"], c["from_pub_id"], c["relation_type"]) for c in sink.calls] == [
        (_HOMEPAGE, _RUN, "own_site_page"),
        (_ABOUT, "ans_aaa", "own_site_snapshot"),
        (_PRODUCTS, _RUN, "own_site_page"),
    ]
    # capture_time 固定为 run.created_at；evidence_pub_id 确定性派生
    assert all(c["capture_time"] == _RUN_CREATED_AT for c in sink.calls)
    assert result.captured[1].evidence_pub_id == derive_evidence_pub_id(
        _TENANT, _RUN, _ABOUT, "own_site_snapshot", "text"
    )
    assert result.captured[0].bytes > 0


def test_main_flow_deterministic_across_runs() -> None:
    def _run_once() -> list[tuple[str, str, str, int]]:
        sink = _FakeSink()
        result = execute_own_site_capture(
            _item(),
            config=_config(),
            loader=_FakeLoader(_context(tasks=list(_TASKS))),
            session_factory=_factory(_FakeSession(pages=_main_flow_pages())),
            sink=sink,
            sleep=lambda s: None,
        )
        return [(c.url, c.kind, c.evidence_pub_id, c.bytes) for c in result.captured]

    assert _run_once() == _run_once()


def test_homepage_failure_still_captures_citations() -> None:
    session = _FakeSession(
        pages={_ABOUT: _page(_ABOUT), _NEWS_POST: _page(_NEWS_POST)},
        errors={_HOMEPAGE: RuntimeError("connection reset")},
    )
    result = execute_own_site_capture(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(tasks=list(_TASKS))),
        session_factory=_factory(session),
        sink=_FakeSink(),
        sleep=lambda s: None,
    )
    assert [c.url for c in result.captured] == [_ABOUT, _NEWS_POST]
    assert [f.url for f in result.failures] == [_HOMEPAGE]
    assert all(c.kind == "own_site_snapshot" for c in result.captured)


def test_homepage_cited_gets_citation_identity() -> None:
    cited_home = "https://example.com/"
    tasks = [("ans_aaa", [{"url": cited_home}])]  # 引用页即主页（归一化后同 URL）
    session = _FakeSession(pages={cited_home: _page(cited_home, links=[])})
    sink = _FakeSink()
    result = execute_own_site_capture(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(tasks=tasks)),
        session_factory=_factory(session),
        sink=sink,
        sleep=lambda s: None,
    )
    # 只抓一次（按被引用原文 URL），按引用页身份存证（a∩b 引用优先）
    assert session.fetched == [cited_home]
    assert [(c.url, c.kind) for c in result.captured] == [(cited_home, "own_site_snapshot")]
    assert sink.calls[0]["from_pub_id"] == "ans_aaa"
    assert sink.calls[0]["relation_type"] == "own_site_snapshot"


def test_persist_failure_recorded_not_raised() -> None:
    pages = _main_flow_pages()
    pages[_NEWS_POST] = _page(_NEWS_POST)
    session = _FakeSession(pages=pages)
    sink = _FakeSink(fail_on={_ABOUT})
    result = execute_own_site_capture(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(tasks=list(_TASKS))),
        session_factory=_factory(session),
        sink=sink,
        sleep=lambda s: None,
    )
    assert [c.url for c in result.captured] == [_HOMEPAGE, _NEWS_POST, _PRODUCTS]
    assert [(f.url) for f in result.failures] == [_ABOUT]
    assert result.failures[0].error.startswith("persist: RuntimeError")


def test_disabled_skips_before_any_io() -> None:
    loader = _FakeLoader(_context())

    def _boom_factory(config: OwnSiteSnapshotConfig) -> _FakeSession:
        raise AssertionError("disabled 时不得启动浏览器")

    result = execute_own_site_capture(
        _item(),
        config=_config(enabled=False),
        loader=loader,
        session_factory=_boom_factory,
        sink=_FakeSink(),
    )
    assert result.skipped == "disabled"
    assert result.captured == [] and result.failures == []
    assert loader.calls == 0


def test_no_website_skips() -> None:
    def _boom_factory(config: OwnSiteSnapshotConfig) -> _FakeSession:
        raise AssertionError("no_website 时不得启动浏览器")

    result = execute_own_site_capture(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(website=None)),
        session_factory=_boom_factory,
        sink=_FakeSink(),
    )
    assert result.skipped == "no_website"
    assert result.captured == [] and result.failures == []


def test_run_not_found_is_non_retryable() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        execute_own_site_capture(
            _item(),
            config=_config(),
            loader=_FakeLoader(None),
            session_factory=_factory(_FakeSession()),
            sink=_FakeSink(),
        )
    assert exc_info.value.type == "run_not_found"
    assert exc_info.value.non_retryable is True


async def test_run_own_site_snapshots_async_inline() -> None:
    beats: list[dict[str, Any]] = []
    result = await run_own_site_snapshots(
        _item(),
        config=_config(),
        loader=_FakeLoader(_context(tasks=list(_TASKS))),
        sink=_FakeSink(),
        session_factory=_factory(_FakeSession(pages=_main_flow_pages())),
        heartbeat=beats.append,
        sleep=lambda s: None,
    )
    assert len(result.captured) == 3
    assert beats and beats[0]["run_pub_id"] == _RUN
