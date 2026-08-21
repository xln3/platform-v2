"""官网诊断建议单测（generate_site_audit_suggestions）。

覆盖：own_site 判定口径（www/裸域/子域互配，与 analytics 同口径复制件）、建议程序
校验（枚举词表/空字段丢弃、evidence_url 必须精确映射本 run own_site 文档否则置
NULL、整批上限 10）、execute 主流程（disabled / no_own_site_host / no_own_site_
documents / llm_unavailable 零调用零落库 / already_generated 批次幂等 / CAS 失败
剔除 / 全部丢弃不落空批次）。依赖全 fake，绝不打真 LLM/DB/MinIO。
"""

from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.site_suggestions import (
    PROMPT_VERSION,
    OwnSiteDocument,
    SiteDocumentRow,
    SiteSuggestionsContext,
    SiteSuggestionsInput,
    SiteSuggestionsResult,
    SuggestionsError,
    _host_from_website,
    _is_own_site,
    derive_batch_pub_id,
    derive_suggestion_pub_id,
    execute_site_suggestions,
    parse_suggestions_payload,
    validate_suggestions,
)
from workflows.activities.source_audit import AuditLlmConfig

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_RUN = "run_0123456789abcdef"

_ITEM = SiteSuggestionsInput(tenant_pub_id=_TENANT, project_pub_id=_PROJECT, run_pub_id=_RUN)
_LLM = AuditLlmConfig(api_key="k", model="gpt-5.6-luna", base_url="https://aihubmix.com")
_LLM_NO_KEY = AuditLlmConfig(api_key="", model="gpt-5.6-luna", base_url="https://aihubmix.com")

_OWN_URL = "https://www.webray.com.cn/product/raytag"
_OTHER_URL = "https://a.example.com/article"


def _item(
    category: str = "citability",
    severity: str = "high",
    title: str = "t",
    detail: str = "d",
    evidence_url: str = "",
) -> dict:
    return {
        "category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence_url": evidence_url,
    }


def _doc_row(pub_id: str, url: str, host: str) -> SiteDocumentRow:
    return SiteDocumentRow(
        pub_id=pub_id,
        url=url,
        host=host,
        text_cas_key=f"cas/{pub_id}",
        text_sha256="a" * 64,
        transcript_verdict="accurate",
        transcript_rationale="引述与正文一致",
        factual_verdict="unsupported",
        factual_rationale="正文未涉及已确认事实",
    )


def _context(
    *,
    own_site_host: str | None = "www.webray.com.cn",
    documents: list[SiteDocumentRow] | None = None,
) -> SiteSuggestionsContext:
    if documents is None:
        documents = [
            _doc_row("srd_own", _OWN_URL, "www.webray.com.cn"),
            _doc_row("srd_other", _OTHER_URL, "a.example.com"),
        ]
    return SiteSuggestionsContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        project_pub_id=_PROJECT,
        run_id="00000000-0000-0000-0000-000000000003",
        run_pub_id=_RUN,
        brand="盛邦安全",
        own_site_host=own_site_host,
        documents=documents,
    )


class _FakeLoader:
    def __init__(self, context: SiteSuggestionsContext | None) -> None:
        self._context = context

    def load(
        self, tenant_pub_id: str, run_pub_id: str, project_pub_id: str
    ) -> SiteSuggestionsContext | None:
        return self._context


class _FakeTextStore:
    def __init__(self, text: str = "官网正文要点" * 100, fail_keys: set[str] | None = None) -> None:
        self._text = text
        self._fail_keys = fail_keys or set()

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        if object_key in self._fail_keys:
            raise RuntimeError("cas boom")
        return self._text


class _FakeJudge:
    def __init__(self, items: list[dict] | None = None, error: Exception | None = None) -> None:
        self._items = (
            items
            if items is not None
            else [
                _item(
                    title="产品页缺结构化要点",
                    detail="正文长段落无列表，AI 摘引困难，建议拆 FAQ。",
                    evidence_url=_OWN_URL,
                )
            ]
        )
        self._error = error
        self.calls = 0

    def suggest(
        self, *, brand: str, own_site_host: str, documents: list[OwnSiteDocument]
    ) -> list[dict]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._items


class _FakeSink:
    def __init__(self, exists: bool = False) -> None:
        self._exists = exists
        self.batches: list[tuple[str, list]] = []

    def batch_exists(self, *, context: SiteSuggestionsContext, batch_pub_id: str) -> bool:
        return self._exists

    def persist_batch(
        self,
        *,
        context: SiteSuggestionsContext,
        batch_pub_id: str,
        model: str,
        drafts: list,
    ) -> int:
        self.batches.append((batch_pub_id, list(drafts)))
        return len(drafts)


def _execute(
    *,
    context: SiteSuggestionsContext | None,
    judge: _FakeJudge | None = None,
    llm: AuditLlmConfig = _LLM,
    sink: _FakeSink | None = None,
    text_store: _FakeTextStore | None = None,
    enabled: bool = True,
) -> tuple[SiteSuggestionsResult, _FakeSink, _FakeJudge]:
    used_sink = sink or _FakeSink()
    used_judge = judge or _FakeJudge()
    result = execute_site_suggestions(
        _ITEM,
        enabled=enabled,
        llm=llm,
        judge=used_judge,
        loader=_FakeLoader(context),
        text_store=text_store or _FakeTextStore(),
        sink=used_sink,
    )
    return result, used_sink, used_judge


# ---------------------------------------------------------------------------
# own_site 判定口径（analytics 同口径复制件的行为锁定）
# ---------------------------------------------------------------------------


def test_is_own_site_host_matching() -> None:
    own = "www.webray.com.cn"
    assert _is_own_site("www.webray.com.cn", own)
    assert _is_own_site("webray.com.cn", own)  # 裸域互配
    assert _is_own_site("docs.webray.com.cn", own)  # 子域
    assert _is_own_site("WEBRAY.COM.CN", own)  # 大小写不敏感
    assert not _is_own_site("webray.com.cn.evil.com", own)
    assert not _is_own_site("a.example.com", own)
    assert not _is_own_site("webray.com.cn", None)  # 官网未知一律 False
    assert not _is_own_site("", own)


def test_host_from_website() -> None:
    assert _host_from_website("https://www.webray.com.cn/path?q=1") == "www.webray.com.cn"
    assert _host_from_website("webray.com.cn") == "webray.com.cn"
    assert _host_from_website("") is None
    assert _host_from_website(None) is None


# ---------------------------------------------------------------------------
# 建议程序校验
# ---------------------------------------------------------------------------


def test_validate_suggestions_drops_bad_enums_and_empty_fields() -> None:
    drafts, dropped, evidence_dropped, truncated = validate_suggestions(
        [
            _item(),
            _item(category="bogus"),
            _item(severity="severe"),
            _item(category="other", severity="low", title=""),
            _item(category="other", severity="low", detail=""),
        ],
        evidence_pub_by_url={},
    )
    assert len(drafts) == 1 and drafts[0].category == "citability"
    assert dropped == 4 and evidence_dropped == 0 and truncated == 0


def test_validate_suggestions_evidence_must_map_to_input_documents() -> None:
    drafts, dropped, evidence_dropped, _ = validate_suggestions(
        [
            _item(
                category="fact_consistency",
                severity="medium",
                title="参数页与确认事实矛盾",
                detail="官网写 40 种，确认为 120 种。",
                evidence_url=_OWN_URL,
            ),
            _item(
                category="other",
                severity="low",
                title="站外证据一律置空",
                detail="LLM 给了项目外 URL。",
                evidence_url="https://evil.example.com/x",
            ),
        ],
        evidence_pub_by_url={_OWN_URL: "srd_own"},
    )
    assert dropped == 0
    assert drafts[0].evidence_document_pub_id == "srd_own"
    assert drafts[1].evidence_document_pub_id is None
    assert evidence_dropped == 1


def test_validate_suggestions_caps_at_ten() -> None:
    items = [_item(category="other", severity="low", title=f"建议{i}") for i in range(12)]
    drafts, _, _, truncated = validate_suggestions(items, evidence_pub_by_url={})
    assert len(drafts) == 10 and truncated == 2


def test_parse_suggestions_payload_strict() -> None:
    good_text = (
        '{"suggestions":[{"category":"other","severity":"low",'
        '"title":"t","detail":"d","evidence_url":""}]}'
    )
    good = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": good_text}],
            }
        ]
    }
    assert len(parse_suggestions_payload(good)) == 1
    with pytest.raises(SuggestionsError):
        parse_suggestions_payload({"output": []})
    bad = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "not json"}],
            }
        ]
    }
    with pytest.raises(SuggestionsError):
        parse_suggestions_payload(bad)


def test_derive_ids_deterministic() -> None:
    batch = derive_batch_pub_id(_TENANT, _RUN, "m", PROMPT_VERSION)
    assert batch == derive_batch_pub_id(_TENANT, _RUN, "m", PROMPT_VERSION)
    assert batch != derive_batch_pub_id(_TENANT, "run_other", "m", PROMPT_VERSION)
    assert derive_suggestion_pub_id(batch, 0) != derive_suggestion_pub_id(batch, 1)


# ---------------------------------------------------------------------------
# execute 主流程
# ---------------------------------------------------------------------------


def test_execute_disabled_zero_io() -> None:
    result, sink, judge = _execute(context=None, enabled=False)
    assert result.disabled is True
    assert sink.batches == [] and judge.calls == 0


def test_execute_run_not_found_non_retryable() -> None:
    with pytest.raises(ApplicationError, match="run not found"):
        _execute(context=None)


def test_execute_skips_when_no_own_site_host() -> None:
    result, sink, judge = _execute(context=_context(own_site_host=None))
    assert result.skipped == "no_own_site_host"
    assert sink.batches == [] and judge.calls == 0


def test_execute_skips_when_no_own_site_documents() -> None:
    context = _context(documents=[_doc_row("srd_other", _OTHER_URL, "a.example.com")])
    result, sink, judge = _execute(context=context)
    assert result.skipped == "no_own_site_documents"
    assert sink.batches == [] and judge.calls == 0


def test_execute_llm_unavailable_zero_io() -> None:
    result, sink, judge = _execute(context=_context(), llm=_LLM_NO_KEY)
    assert result.llm_unavailable is True
    assert result.own_site_documents == 1
    assert sink.batches == [] and judge.calls == 0


def test_execute_already_generated_skips_before_llm() -> None:
    result, sink, judge = _execute(context=_context(), sink=_FakeSink(exists=True))
    assert result.skipped == "already_generated"
    assert result.batch_pub_id  # 批次 id 已确定性派生
    assert judge.calls == 0 and sink.batches == []


def test_execute_happy_path_persists_batch() -> None:
    result, sink, judge = _execute(context=_context())
    assert judge.calls == 1
    assert result.suggestions == 1
    assert len(sink.batches) == 1
    batch_pub_id, drafts = sink.batches[0]
    expected = derive_batch_pub_id(_TENANT, _RUN, "gpt-5.6-luna", PROMPT_VERSION)
    assert batch_pub_id == expected
    assert drafts[0].evidence_document_pub_id == "srd_own"  # 证据映射到本项目文档


def test_execute_batches_more_than_ten_official_pages_without_dropping_scope() -> None:
    documents = [_doc_row("srd_own", _OWN_URL, "www.webray.com.cn")]
    documents.extend(
        _doc_row(
            f"srd_{index}",
            f"https://www.webray.com.cn/page-{index}",
            "www.webray.com.cn",
        )
        for index in range(1, 25)
    )

    result, sink, judge = _execute(context=_context(documents=documents))

    assert result.own_site_documents == 25
    assert judge.calls == 3
    assert result.truncated == 0
    assert len(sink.batches) == 1


def test_execute_drops_invalid_and_counts_honestly() -> None:
    judge = _FakeJudge(
        items=[
            _item(category="bogus", title="坏枚举"),
            _item(
                category="crawlability",
                severity="medium",
                title="页正文过短",
                detail="疑似渲染问题。",
                evidence_url="https://evil.example.com/x",
            ),
        ]
    )
    result, sink, _ = _execute(context=_context(), judge=judge)
    assert result.dropped == 1
    assert result.evidence_dropped == 1
    assert result.suggestions == 1
    assert sink.batches[0][1][0].evidence_document_pub_id is None


def test_execute_cas_failure_excludes_document() -> None:
    store = _FakeTextStore(fail_keys={"cas/srd_own"})
    result, sink, judge = _execute(context=_context(), text_store=store)
    # 唯一 own_site 文档 CAS 读失败 → 剔除后无文档可审，安静跳过
    assert result.skipped == "no_own_site_documents"
    assert len(result.failures) == 1
    assert judge.calls == 0 and sink.batches == []


def test_execute_all_dropped_persists_nothing() -> None:
    judge = _FakeJudge(items=[_item(category="bogus")])
    result, sink, _ = _execute(context=_context(), judge=judge)
    assert result.dropped == 1 and result.suggestions == 0
    assert sink.batches == []  # 不落空批次，下轮可重试


def test_execute_llm_error_leaves_failures() -> None:
    judge = _FakeJudge(error=SuggestionsError("LLM 响应非 JSON"))
    result, sink, _ = _execute(context=_context(), judge=judge)
    assert result.suggestions == 0 and len(result.failures) == 1
    assert sink.batches == []
