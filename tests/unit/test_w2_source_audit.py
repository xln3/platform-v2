"""W2 核对层（audit_run_sources）单元测试。

verbatim 校验/已确认事实收集/prompt 构造纯函数直测；主流程依赖注入
fake judge/loader/text_store/sink，绝不打真 LLM/DB/MinIO。覆盖：
双口径正反例（verbatim 通过 / 篡改 quote 被丢弃落 validation_failure）、
unverifiable / no_confirmed_facts / llm_unavailable / llm_error 路径、
幂等键跳过、disabled 开关。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from temporalio.exceptions import ApplicationError

from workflows.activities.source_audit import (
    PROMPT_VERSION,
    AuditDocument,
    AuditJudge,
    AuditLlmConfig,
    AuditRecord,
    JudgeError,
    JudgeOutcome,
    RunAuditContext,
    SourceAuditInput,
    SourceAuditResult,
    SourceTextStore,
    collect_confirmed_facts,
    derive_audit_pub_id,
    execute_source_audit,
    normalize_verbatim,
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
    def __init__(self, text: str = _SOURCE_TEXT, error: Exception | None = None) -> None:
        self._text = text
        self._error = error

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        if self._error is not None:
            raise self._error
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
    a = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "transcript", "m", PROMPT_VERSION)
    b = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "transcript", "m", PROMPT_VERSION)
    c = derive_audit_pub_id(_TENANT, _RUN, "srd_x", "factual", "m", PROMPT_VERSION)
    assert a == b and a.startswith("sra_") and len(a) == 30 and a != c


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
        assert record.prompt_version == PROMPT_VERSION
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
    context = _context(confirmed_facts=[])
    _result, sink = _execute(context=context)
    factual = next(r for r in sink.records if r.dimension == "factual")
    assert factual.audit_status == "no_confirmed_facts"
    assert factual.verdict is None
    transcript = next(r for r in sink.records if r.dimension == "transcript")
    assert transcript.audit_status == "ok"  # 口径A 不受影响


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
            (doc.pub_id, "transcript", _LLM.model, PROMPT_VERSION),
            (doc.pub_id, "factual", _LLM.model, PROMPT_VERSION),
        }
    )
    judge = _FakeJudge()
    result, sink = _execute(context=_context(existing_keys=keys), judge=judge)
    assert judge.calls == [] and sink.records == []
    assert len(result.skipped) == 2
    assert {s.reason for s in result.skipped} == {"already_audited"}


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
