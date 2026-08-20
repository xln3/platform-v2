"""W2 核对层（audit_run_sources）单元测试。

verbatim 校验/已确认事实收集/官网语料装配/prompt 构造纯函数直测；主流程依赖注入
fake judge/loader/text_store/sink，绝不打真 LLM/DB/MinIO。覆盖：
双口径正反例（verbatim 通过 / 篡改 quote 被丢弃落 validation_failure）、
unverifiable / no_confirmed_facts / llm_unavailable / llm_error 路径、
官网语料事实基底（仅语料可判 / 语料+确认事实叠加 / 语料读失败诚实降级）、
幂等键跳过（口径B 升 v2 后旧 v1 键不拦新判）、disabled 开关。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.source_audit import (
    PROMPT_VERSION_FACTUAL,
    PROMPT_VERSION_TRANSCRIPT,
    AuditDocument,
    AuditJudge,
    AuditLlmConfig,
    AuditRecord,
    JudgeError,
    JudgeOutcome,
    OfficialSiteAsset,
    OfficialSitePage,
    RunAuditContext,
    SourceAuditInput,
    SourceAuditResult,
    SourceTextStore,
    _ResponsesApiJudge,
    build_fact_base_blob,
    build_official_site_corpus,
    collect_confirmed_facts,
    derive_audit_pub_id,
    execute_source_audit,
    normalize_verbatim,
    prompt_version_for,
    quote_is_verbatim,
    validate_judgment,
)

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_RUN = "run_0123456789abcdef"
_RUN_CREATED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

_ITEM = SourceAuditInput(tenant_pub_id=_TENANT, project_pub_id=_PROJECT, run_pub_id=_RUN)
_LLM = AuditLlmConfig(api_key="k", model="gpt-5.6-luna", base_url="https://aihubmix.com")
_LLM_NO_KEY = AuditLlmConfig(api_key="", model="gpt-5.6-luna", base_url="https://aihubmix.com")

_SOURCE_TEXT = (
    "中意人寿保险有限公司成立于二零零二年，注册资本三十七亿元人民币，"
    "由中国石油天然气集团与意大利忠利保险合资组建。"
    "公司最新推出的重疾险覆盖一百二十种疾病，包含轻症豁免保费责任。"
)
_ANSWER_BLOB = "中意人寿的重疾险覆盖一百二十种疾病，包含轻症豁免保费责任。"
_FACT_BLOB = "核心卖点：重疾险覆盖一百二十种疾病，轻症豁免保费。"

_GOOD_JUDGMENT = JudgeOutcome(
    verdict="accurate",
    quote_source="重疾险覆盖一百二十种疾病，包含轻症豁免保费责任",
    quote_answer="重疾险覆盖一百二十种疾病",
    rationale="引述与正文一致。",
)

# quote_answer 逐字摘自官网语料（_SITE_PAGE_TEXT）的判定
_SITE_JUDGMENT = JudgeOutcome(
    verdict="accurate",
    quote_source="重疾险覆盖一百二十种疾病，包含轻症豁免保费责任",
    quote_answer="盛邦安全是网络空间资产测绘与网络安全厂商",
    rationale="官网首页支持该定位描述。",
)


_SITE_PAGE_TEXT = "盛邦安全是网络空间资产测绘与网络安全厂商，产品覆盖资产搜索引擎。"
_SITE_ASSET = OfficialSiteAsset(
    pub_id="evd_site1",
    source_url="https://www.webray.com.cn/",
    object_key="cas/site/1",
    sha256="b" * 64,
)


def _site_payload(text: str = _SITE_PAGE_TEXT) -> str:
    return json.dumps(
        {"url": "https://www.webray.com.cn/", "title": "首页", "text": text},
        ensure_ascii=False,
    )


def _doc(
    url: str = "https://a.example.com/article",
    *,
    extract_status: str = "ok",
    pub_id: str = "srd_doc1",
) -> AuditDocument:
    return AuditDocument(
        pub_id=pub_id,
        url=url,
        host="a.example.com",
        extract_status=extract_status,
        text_cas_key="cas/key/1" if extract_status == "ok" else None,
        text_sha256="a" * 64 if extract_status == "ok" else None,
    )


def _context(
    *,
    documents: list[AuditDocument] | None = None,
    citations_by_url: dict[str, list[str]] | None = None,
    confirmed_facts: list[str] | None = None,
    existing_keys: frozenset[tuple[str, str, str, str]] = frozenset(),
    official_site_host: str | None = None,
    official_site_assets: list[OfficialSiteAsset] | None = None,
) -> RunAuditContext:
    return RunAuditContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        run_id="00000000-0000-0000-0000-000000000003",
        run_pub_id=_RUN,
        project_pub_id=_PROJECT,
        created_at=_RUN_CREATED_AT,
        documents=documents if documents is not None else [_doc()],
        citations_by_url=(
            citations_by_url
            if citations_by_url is not None
            else {"https://a.example.com/article": [_ANSWER_BLOB]}
        ),
        confirmed_facts=confirmed_facts if confirmed_facts is not None else [_FACT_BLOB],
        existing_keys=existing_keys,
        official_site_host=official_site_host,
        official_site_assets=official_site_assets or [],
    )


class _FakeLoader:
    def __init__(self, context: RunAuditContext | None) -> None:
        self._context = context

    def load(
        self, tenant_pub_id: str, run_pub_id: str, project_pub_id: str
    ) -> RunAuditContext | None:
        return self._context


class _FakeJudge:
    def __init__(
        self,
        outcomes: dict[str, JudgeOutcome] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._outcomes = outcomes or {}
        self._errors = errors or {}
        self.calls: list[str] = []

    def judge(
        self, *, dimension: str, url: str, source_text: str, answer_blob: str
    ) -> JudgeOutcome:
        self.calls.append(dimension)
        if dimension in self._errors:
            raise self._errors[dimension]
        return self._outcomes.get(dimension, _GOOD_JUDGMENT)


class _FakeTextStore:
    def __init__(
        self,
        text: str = _SOURCE_TEXT,
        error: Exception | None = None,
        by_key: dict[str, str] | None = None,
    ) -> None:
        self._text = text
        self._error = error
        self._by_key = by_key

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        if self._error is not None:
            raise self._error
        if self._by_key is not None:
            return self._by_key[object_key]
        return self._text


class _FakeSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def persist(self, *, context: RunAuditContext, record: AuditRecord) -> str:
        self.records.append(record)
        return derive_audit_pub_id(
            context.tenant_pub_id,
            context.run_pub_id,
            record.source_document_pub_id,
            record.dimension,
            record.model,
            record.prompt_version,
        )


def _execute(
    *,
    context: RunAuditContext | None,
    judge: AuditJudge | None = None,
    llm: AuditLlmConfig = _LLM,
    sink: _FakeSink | None = None,
    text_store: SourceTextStore | None = None,
    enabled: bool = True,
) -> tuple[SourceAuditResult, _FakeSink]:
    used_sink = sink or _FakeSink()
    result = execute_source_audit(
        _ITEM,
        enabled=enabled,
        llm=llm,
        judge=judge if judge is not None else _FakeJudge(),
        loader=_FakeLoader(context),
        text_store=text_store or _FakeTextStore(),
        sink=used_sink,
    )
    return result, used_sink


# ---------------------------------------------------------------------------
# verbatim 校验
# ---------------------------------------------------------------------------


def test_normalize_verbatim_collapses_whitespace() -> None:
    assert normalize_verbatim("a  b\n\nc　d") == "a b c d"


def test_quote_is_verbatim_substring_with_whitespace_normalization() -> None:
    assert quote_is_verbatim("覆盖一百二十种\n疾病", "覆盖一百二十种 疾病，轻症豁免")
    assert not quote_is_verbatim("", "任何文本")
    assert not quote_is_verbatim("不存在的话", "覆盖一百二十种疾病")


def _validate(judgment: JudgeOutcome) -> str | None:
    return validate_judgment(judgment, source_text=_SOURCE_TEXT, answer_blob=_ANSWER_BLOB)


def test_validate_judgment_accepts_verbatim_quotes() -> None:
    assert _validate(_GOOD_JUDGMENT) is None


def test_validate_judgment_rejects_tampered_quotes() -> None:
    bad_source = JudgeOutcome("accurate", "正文里没有这句话", "重疾险覆盖一百二十种疾病", "r")
    assert _validate(bad_source) is not None
    bad_answer = JudgeOutcome("accurate", "重疾险覆盖一百二十种疾病", "引述里没有这句话", "r")
    assert _validate(bad_answer) is not None


def test_validate_judgment_requires_quotes_for_accurate_and_inaccurate() -> None:
    empty_quotes = JudgeOutcome("accurate", "", "", "r")
    assert _validate(empty_quotes) is not None


def test_validate_judgment_allows_empty_quotes_for_unsupported() -> None:
    unsupported = JudgeOutcome("unsupported", "", "", "正文未涉及。")
    assert _validate(unsupported) is None


def test_validate_judgment_rejects_illegal_verdict() -> None:
    illegal = JudgeOutcome("half-true", "", "", "r")
    assert _validate(illegal) is not None


# ---------------------------------------------------------------------------
# 已确认事实收集
# ---------------------------------------------------------------------------


def test_collect_confirmed_facts_requires_truth_confirmed() -> None:
    profile = {"truth_confirmed": False, "selling_points": "卖点", "licenses": []}
    assert collect_confirmed_facts(profile) == []
    assert collect_confirmed_facts(None) == []
    assert collect_confirmed_facts({}) == []


def test_collect_confirmed_facts_collects_selling_points_and_licenses() -> None:
    profile = {
        "truth_confirmed": True,
        "selling_points": " 重疾险覆盖一百二十种疾病 ",
        "licenses": [{"name": "保险许可证", "no": "P10001"}, {"name": "", "no": ""}, "bad"],
    }
    facts = collect_confirmed_facts(profile)
    assert facts == [
        "核心卖点：重疾险覆盖一百二十种疾病",
        "资质：name=保险许可证，no=P10001",
    ]


# ---------------------------------------------------------------------------
# pub_id 派生确定性
# ---------------------------------------------------------------------------


def test_derive_audit_pub_id_deterministic() -> None:
    a = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "transcript", "m", PROMPT_VERSION_TRANSCRIPT)
    b = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "transcript", "m", PROMPT_VERSION_TRANSCRIPT)
    c = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "factual", "m", PROMPT_VERSION_FACTUAL)
    assert a == b and a.startswith("sra_") and len(a) == 30 and a != c


# ---------------------------------------------------------------------------
# 官网语料装配 / 事实基底拼装
# ---------------------------------------------------------------------------


def test_prompt_version_split_by_dimension() -> None:
    assert prompt_version_for("transcript") == "source-audit-v1"
    assert prompt_version_for("factual") == "source-audit-v2"


def test_build_official_site_corpus_homepage_first_and_skips_empty() -> None:
    pages = [
        OfficialSitePage("https://www.webray.com.cn/product.html", "产品", "产品页正文"),
        OfficialSitePage("https://www.webray.com.cn/", "首页", "首页正文"),
        OfficialSitePage("https://www.webray.com.cn/empty.html", "空页", "   "),
    ]
    corpus = build_official_site_corpus(pages)
    assert corpus.startswith("【官网页 https://www.webray.com.cn/】")
    assert "产品页正文" in corpus and "首页正文" in corpus
    assert "empty.html" not in corpus
    assert build_official_site_corpus([]) == ""


def test_build_official_site_corpus_truncates_oversized() -> None:
    pages = [
        OfficialSitePage(f"https://www.webray.com.cn/p{i}.html", "t", "x" * 10_000)
        for i in range(20)
    ]
    corpus = build_official_site_corpus(pages)
    assert len(corpus) <= 24_000


def test_build_fact_base_blob_layers() -> None:
    assert build_fact_base_blob([], "") == ""
    only_facts = build_fact_base_blob(["核心卖点：X"], "")
    assert "【客户已确认事实】" in only_facts and "官网" not in only_facts
    only_corpus = build_fact_base_blob([], "【官网页 u】\n正文")
    assert "【客户官网公开信息】" in only_corpus and "已确认事实" not in only_corpus
    both = build_fact_base_blob(["核心卖点：X"], "【官网页 u】\n正文")
    assert both.index("【客户已确认事实】") < both.index("【客户官网公开信息】")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def test_execute_audit_disabled_zero_io() -> None:
    sink = _FakeSink()
    result = execute_source_audit(
        _ITEM,
        enabled=False,
        llm=_LLM,
        judge=_FakeJudge(),
        loader=_FakeLoader(None),
        text_store=_FakeTextStore(),
        sink=sink,
    )
    assert result.disabled is True and sink.records == []


def test_execute_audit_run_not_found_raises() -> None:
    with pytest.raises(ApplicationError, match="run not found"):
        _execute(context=None)


def test_execute_audit_two_dimensions_happy_path() -> None:
    result, sink = _execute(context=_context())
    assert result.failures == [] and result.skipped == []
    assert len(sink.records) == 2
    dimensions = {record.dimension: record for record in sink.records}
    assert set(dimensions) == {"transcript", "factual"}
    for record in sink.records:
        assert record.audit_status == "ok"
        assert record.verdict == "accurate"
        assert record.model == _LLM.model
    assert dimensions["transcript"].prompt_version == PROMPT_VERSION_TRANSCRIPT
    assert dimensions["factual"].prompt_version == PROMPT_VERSION_FACTUAL
    assert {a.dimension for a in result.audited} == {"transcript", "factual"}


def test_execute_audit_tampered_quote_drops_verdict() -> None:
    tampered = JudgeOutcome("inaccurate", "编造的正文引用", "编造的引述引用", "模型自述理由")
    judge = _FakeJudge(outcomes={"transcript": tampered})
    result, sink = _execute(context=_context(), judge=judge)
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "validation_failure"
    assert transcript.verdict is None  # 判分被丢弃，绝不入分布
    assert "逐字校验未过" in (transcript.rationale or "")
    assert transcript.quote_source == "编造的正文引用"  # 问题 quote 如实留痕
    # factual 口径不受影响
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "ok"
    assert result.failures == []


def test_execute_audit_unverifiable_when_fetch_failed() -> None:
    doc = _doc(extract_status="http_error")
    context = _context(documents=[doc])
    result, sink = _execute(context=context)
    assert len(sink.records) == 2
    for record in sink.records:
        assert record.audit_status == "unverifiable"
        assert record.verdict == "unverifiable"
        assert "http_error" in (record.rationale or "")
    assert result.failures == []


def test_execute_audit_unverifiable_when_no_cited_text() -> None:
    context = _context(citations_by_url={})  # 无引述
    _result, sink = _execute(context=context)
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "unverifiable"
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "ok"  # 口径B 不受影响


def test_execute_audit_no_confirmed_facts() -> None:
    context = _context(confirmed_facts=[])  # 无确认事实且无官网快照资产
    _result, sink = _execute(context=context)
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "no_confirmed_facts"
    assert factual.verdict is None
    assert "官网语料" in (factual.rationale or "")
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "ok"  # 口径A 不受影响


def test_execute_audit_factual_with_official_site_corpus_only() -> None:
    """intake 无确认事实但有官网快照 → 口径B 用官网语料照常判定（不再降级）。"""
    judge = _FakeJudge(outcomes={"factual": _SITE_JUDGMENT})
    context = _context(
        confirmed_facts=[],
        official_site_host="www.webray.com.cn",
        official_site_assets=[_SITE_ASSET],
    )
    store = _FakeTextStore(by_key={"cas/key/1": _SOURCE_TEXT, "cas/site/1": _site_payload()})
    _result, sink = _execute(context=context, judge=judge, text_store=store)
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "ok"
    assert factual.prompt_version == PROMPT_VERSION_FACTUAL
    assert factual.quote_answer == _SITE_JUDGMENT.quote_answer


def test_execute_audit_factual_blob_contains_corpus_and_facts() -> None:
    """确认事实 + 官网语料叠加：送判 blob 两节俱全（fake judge 截获 answer_blob）。"""
    seen: list[str] = []

    class _CapturingJudge(_FakeJudge):
        def judge(self, *, dimension: str, url: str, source_text: str, answer_blob: str):
            if dimension == "factual":
                seen.append(answer_blob)
            return super().judge(
                dimension=dimension, url=url, source_text=source_text, answer_blob=answer_blob
            )

    context = _context(
        official_site_host="www.webray.com.cn",
        official_site_assets=[_SITE_ASSET],
    )
    store = _FakeTextStore(by_key={"cas/key/1": _SOURCE_TEXT, "cas/site/1": _site_payload()})
    _result, sink = _execute(context=context, judge=_CapturingJudge(), text_store=store)
    assert len(seen) == 1
    assert "【客户已确认事实】" in seen[0] and _FACT_BLOB.removeprefix("核心卖点：")[:8] in seen[0]
    assert "【客户官网公开信息】" in seen[0] and "webray.com.cn" in seen[0]
    assert "盛邦安全" in seen[0]
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "ok"


def test_execute_audit_factual_corpus_read_failure_degrades_honestly() -> None:
    """官网快照 CAS 读失败且无确认事实 → 语料空 → no_confirmed_facts（绝不编造）。"""
    context = _context(
        confirmed_facts=[],
        official_site_host="www.webray.com.cn",
        official_site_assets=[_SITE_ASSET],
    )
    store = _FakeTextStore(
        error=RuntimeError("minio down"),
    )
    # 文档正文也读不出 → 进 failures；直接验证语料装载降级路径
    result, sink = _execute(context=context, text_store=store)
    assert sink.records == []
    assert len(result.failures) == 1


def test_execute_audit_factual_corpus_read_failure_with_doc_ok() -> None:
    """文档正文可读、官网快照读失败：口径A 照常，口径B 无确认事实 → no_confirmed_facts。"""

    class _SiteFailStore:
        def get_text(self, object_key: str, expected_sha256: str) -> str:
            if object_key == "cas/site/1":
                raise RuntimeError("minio down")
            return _SOURCE_TEXT

    context = _context(
        confirmed_facts=[],
        official_site_host="www.webray.com.cn",
        official_site_assets=[_SITE_ASSET],
    )
    result, sink = _execute(context=context, text_store=_SiteFailStore())
    assert result.failures == []
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "ok"
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "no_confirmed_facts"


def test_execute_audit_llm_unavailable_when_key_missing() -> None:
    judge = _FakeJudge()
    result, sink = _execute(context=_context(), judge=judge, llm=_LLM_NO_KEY)
    assert judge.calls == []  # 一次 LLM 都不调
    assert len(sink.records) == 2
    for record in sink.records:
        assert record.audit_status == "llm_unavailable"
        assert record.verdict is None
    assert result.failures == []


def test_execute_audit_llm_error_recorded_honestly() -> None:
    judge = _FakeJudge(errors={"transcript": JudgeError("ReadTimeout")})
    _result, sink = _execute(context=_context(), judge=judge)
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "llm_error"
    assert transcript.verdict is None
    assert "ReadTimeout" in (transcript.rationale or "")
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "ok"


def test_execute_audit_idempotent_skip_existing() -> None:
    doc = _doc()
    keys = frozenset(
        {
            (doc.pub_id, "transcript", _LLM.model, PROMPT_VERSION_TRANSCRIPT),
            (doc.pub_id, "factual", _LLM.model, PROMPT_VERSION_FACTUAL),
        }
    )
    judge = _FakeJudge()
    result, sink = _execute(context=_context(existing_keys=keys), judge=judge)
    assert judge.calls == [] and sink.records == []
    assert len(result.skipped) == 2
    assert {s.reason for s in result.skipped} == {"already_audited"}


def test_execute_audit_factual_v1_key_does_not_block_v2_rejudge() -> None:
    """口径B 升版重判语义：旧 v1 判定行仍在幂等键里，但 v2 键不同 → 照常重判；
    口径A v1 未变 → 命中跳过，不重复花 LLM 调用。"""
    doc = _doc()
    keys = frozenset(
        {
            (doc.pub_id, "transcript", _LLM.model, PROMPT_VERSION_TRANSCRIPT),
            (doc.pub_id, "factual", _LLM.model, "source-audit-v1"),  # 旧版残留
        }
    )
    judge = _FakeJudge()
    result, sink = _execute(context=_context(existing_keys=keys), judge=judge)
    assert judge.calls == ["factual"]  # 只重判口径B
    assert len(sink.records) == 1
    assert sink.records[0].dimension == "factual"
    assert sink.records[0].prompt_version == PROMPT_VERSION_FACTUAL
    assert [s.dimension for s in result.skipped] == ["transcript"]


def test_execute_audit_cas_read_failure_goes_to_failures() -> None:
    store = _FakeTextStore(error=RuntimeError("minio down"))
    result, sink = _execute(context=_context(), text_store=store)
    assert sink.records == []
    assert len(result.failures) == 1 and "minio down" in result.failures[0].error


def test_execute_audit_missing_cas_ref_raises_non_retryable() -> None:
    doc = AuditDocument(
        pub_id="srd_broken",
        url="https://a.example.com/article",
        host="a.example.com",
        extract_status="ok",
        text_cas_key=None,  # ok 但缺 CAS 引用 = 数据矛盾
        text_sha256=None,
    )
    with pytest.raises(ApplicationError, match="缺 CAS 引用"):
        _execute(context=_context(documents=[doc]))


class _FailoverFakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"output": []}


class _FailoverFakeClient:
    """httpx.Client 替身：按 base_url 决定成败（主通道 ConnectError，备通道 200）。"""

    instances: list[str] = []
    trust_env_values: list[bool] = []

    def __init__(self, *, base_url: str, headers: dict, timeout: float, trust_env: bool) -> None:
        self.base_url = base_url
        _FailoverFakeClient.instances.append(base_url)
        _FailoverFakeClient.trust_env_values.append(trust_env)

    def __enter__(self) -> _FailoverFakeClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, path: str, json: dict) -> _FailoverFakeResponse:
        if "primary" in self.base_url:
            raise httpx.ConnectError("connect failed")
        return _FailoverFakeResponse()


def test_audit_llm_client_fails_over_to_fallback_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """主通道网络失败 → 自动换 base_url_fallback 重试一次（20260810 aihubmix 直连不通实证）。"""
    _FailoverFakeClient.instances = []
    _FailoverFakeClient.trust_env_values = []
    monkeypatch.setattr("workflows.activities.source_audit.httpx.Client", _FailoverFakeClient)
    client = _ResponsesApiJudge(
        AuditLlmConfig(
            api_key="k",
            model="m",
            base_url="https://primary.example.com",
            base_url_fallback="https://fallback.example.com",
        )
    )
    payload = client._post({"model": "m"})
    assert payload == {"output": []}
    assert _FailoverFakeClient.instances == [
        "https://primary.example.com/v1",
        "https://fallback.example.com/v1",
    ]
    assert _FailoverFakeClient.trust_env_values == [False, False]


def test_audit_llm_client_single_channel_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 fallback 配置时保持单通道（旧行为）：主失败即抛 JudgeError。"""
    _FailoverFakeClient.instances = []
    _FailoverFakeClient.trust_env_values = []
    monkeypatch.setattr("workflows.activities.source_audit.httpx.Client", _FailoverFakeClient)
    client = _ResponsesApiJudge(
        AuditLlmConfig(api_key="k", model="m", base_url="https://primary.example.com")
    )
    with pytest.raises(JudgeError):
        client._post({"model": "m"})
    assert _FailoverFakeClient.instances == ["https://primary.example.com/v1"]
    assert _FailoverFakeClient.trust_env_values == [False]
