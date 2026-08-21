"""Page inspection evidence contract and activity orchestration."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from domain.source_analysis.page_inspection import (
    SourceAnalysisProfile,
    derive_page_inspection_version,
    derive_profile_type,
    validate_finding,
)
from workflows.activities.page_inspection import (
    AnalysisWindow,
    InspectionDocument,
    LinkedAnswer,
    PageCandidateBatch,
    PageInspectionInput,
    PageInspectionRecord,
    RunPageInspectionContext,
    bind_finding_candidate_to_window,
    build_analysis_windows,
    build_transmission_finding,
    compute_transmission,
    execute_page_inspection,
)
from workflows.activities.source_audit import AuditLlmConfig

_TENANT = "tnt_test"
_PROJECT = "prj_test"
_RUN = "run_test"
_PROFILE_PUB = "sap_test"
_DOCUMENT_PUB = "src_test"


def _profile() -> SourceAnalysisProfile:
    return SourceAnalysisProfile(
        pub_id=_PROFILE_PUB,
        object_name="盛邦安全",
        object_kind="brand",
        categories=("WAF",),
        aliases=("盛邦",),
        own_domains=("webray.com.cn",),
        peers=("绿盟科技", "阿里云"),
        anchor_sources=(
            {
                "name": "IDC",
                "publisher": "IDC",
                "url": "https://example.com/idc",
                "categories": ["公有云WAF"],
            },
        ),
        linked_entities=(),
        hard_anchor_available=True,
        decision_mode="selection",
        profile_type="I",
        profile_hash="a" * 64,
    )


def _source_link(quote: str, explanation: str = "该句直接支撑判断。") -> dict[str, Any]:
    return {
        "connector": "because",
        "fact_type": "source_quote",
        "quote": quote,
        "occurrence": 1,
        "explanation": explanation,
    }


def _candidate(
    code: str,
    ledger: str,
    chain: list[dict[str, Any]],
    *,
    summary: str = "页面存在无依据的对象贬评",
) -> dict[str, Any]:
    return {
        "code": code,
        "ledger": ledger,
        "variant": "",
        "summary": summary,
        "action": "review_content",
        "evidence_chain": chain,
        "self_check": {
            "passed": True,
            "reasoning": "若同一句话用于同位对手，仍采用同一判据。",
        },
    }


def test_profile_type_is_derived_from_two_axes() -> None:
    assert derive_profile_type(hard_anchor_available=True, decision_mode="selection") == "I"
    assert derive_profile_type(hard_anchor_available=False, decision_mode="selection") == "II"
    assert derive_profile_type(hard_anchor_available=True, decision_mode="reputation") == "III"
    assert derive_profile_type(hard_anchor_available=False, decision_mode="reputation") == "IV"


def test_analysis_version_changes_with_model_or_prompt() -> None:
    first = derive_page_inspection_version(
        profile_revision=3,
        model="model-a",
        prompt_version="prompt-a",
    )
    assert first == derive_page_inspection_version(
        profile_revision=3,
        model="model-a",
        prompt_version="prompt-a",
    )
    assert first != derive_page_inspection_version(
        profile_revision=3,
        model="model-b",
        prompt_version="prompt-a",
    )
    assert first != derive_page_inspection_version(
        profile_revision=3,
        model="model-a",
        prompt_version="prompt-b",
    )


def test_statement_quote_is_resolved_to_exact_character_span() -> None:
    text = "行业观察称，盛邦安全能力很差，但没有给出测试方法。"
    quote = "盛邦安全能力很差"
    result = validate_finding(
        _candidate("A1", "statement", [_source_link(quote)]),
        source_text=text,
        profile=_profile(),
    )
    assert result.errors == ()
    assert result.finding is not None
    span = result.finding.spans[0]
    assert text[span.text_start : span.text_end] == quote
    assert span.quote_hash == sha256(quote.encode()).hexdigest()


def test_non_verbatim_quote_voids_the_whole_chain() -> None:
    text = "行业观察称，盛邦安全能力很差。"
    result = validate_finding(
        _candidate("A1", "statement", [_source_link("盛邦安全的能力很差")]),
        source_text=text,
        profile=_profile(),
    )
    assert result.finding is None
    assert any("不是正文逐字子串" in error for error in result.errors)


def test_window_occurrence_is_bound_to_the_correct_full_page_interval() -> None:
    quote = "盛邦安全能力很差"
    text = f"{quote}。" + ("中" * 100) + f"第二处：{quote}。"
    second_start = text.rfind(quote)
    window_start = second_start - len("第二处：")
    window = AnalysisWindow(
        start=window_start,
        end=len(text),
        text=text[window_start:],
    )
    bound = bind_finding_candidate_to_window(
        _candidate("A1", "statement", [_source_link(quote)]),
        source_text=text,
        window=window,
    )
    chain = bound["evidence_chain"]
    assert chain[0]["occurrence"] == 2

    result = validate_finding(bound, source_text=text, profile=_profile())
    assert result.finding is not None
    assert result.finding.spans[0].text_start == second_start


def test_exposure_ledger_rejects_legal_claim_and_motive_words() -> None:
    text = "同类厂商包括绿盟科技。"
    candidate = _candidate(
        "C1",
        "exposure",
        [
            _source_link("绿盟科技"),
            {
                "connector": "therefore",
                "fact_type": "absence",
                "search_scope": "source_document_body",
                "search_terms": ["盛邦安全", "盛邦"],
                "operator": "any",
                "match_count": 0,
                "explanation": "正文检索命中为零。",
            },
        ],
        summary="页面故意打压对象",
    )
    result = validate_finding(candidate, source_text=text, profile=_profile())
    assert result.finding is None
    assert "证据链含不可证明的动机词" in result.errors
    assert "暴露账使用了言论/法律主张词" in result.errors


def test_c1_requires_peer_presence_and_recomputed_absence() -> None:
    text = "同类厂商包括绿盟科技、阿里云。"
    result = validate_finding(
        _candidate(
            "C1",
            "exposure",
            [
                _source_link("绿盟科技、阿里云"),
                {
                    "connector": "therefore",
                    "fact_type": "absence",
                    "search_scope": "source_document_body",
                    "search_terms": ["盛邦安全", "盛邦"],
                    "operator": "any",
                    "match_count": 0,
                    "explanation": "在正文范围按档案词逐字检索，命中为零。",
                },
            ],
            summary="同品类名单有同位对手但没有对象",
        ),
        source_text=text,
        profile=_profile(),
    )
    assert result.errors == ()
    assert result.finding is not None
    assert result.finding.ledger == "exposure"


def test_a5_is_kept_as_needs_review_until_factcheck() -> None:
    text = "文章声称盛邦安全伪造了检测数据。"
    result = validate_finding(
        _candidate("A5", "statement", [_source_link("盛邦安全伪造了检测数据")]),
        source_text=text,
        profile=_profile(),
    )
    assert result.finding is not None
    assert result.finding.finding_status == "needs_review"


def test_transmission_metrics_and_deterministic_c1_variant() -> None:
    text = "根据IDC数据，硬件WAF厂商包括盛邦安全。"
    answers = tuple(
        LinkedAnswer(
            pub_id=f"ans_{index}",
            text="阿里云位居榜首。",
            query=f"问题{index}",
            model="doubao",
        )
        for index in range(3)
    )
    metrics = compute_transmission(text, _profile(), answers)
    assert metrics["T1"]["answer_count"] == 3
    assert metrics["T2"]["rate"] == 0.0
    finding = build_transmission_finding(
        text=text,
        profile=_profile(),
        transmission=metrics,
    )
    assert finding is not None
    assert finding.code == "C1"
    assert finding.variant == "transmission"
    assert finding.ledger == "exposure"


def test_window_builder_reports_unseen_tail() -> None:
    windows, truncated = build_analysis_windows("x" * 30_000, max_chars=20_000)
    assert len(windows) == 2
    assert windows[1].start < windows[0].end
    assert truncated == 10_000


class _FakeLoader:
    def __init__(self, context: RunPageInspectionContext | None) -> None:
        self.context = context

    def load(self, *_args: Any, **_kwargs: Any) -> RunPageInspectionContext | None:
        return self.context


class _FakeTextStore:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, _object_key: str, _expected_sha256: str) -> str:
        return self.text


class _FakeJudge:
    def __init__(self, batch: PageCandidateBatch) -> None:
        self.batch = batch
        self.calls: list[AnalysisWindow] = []

    def analyze(self, **kwargs: Any) -> PageCandidateBatch:
        self.calls.append(kwargs["window"])
        return self.batch


class _FakeSink:
    def __init__(self) -> None:
        self.records: list[PageInspectionRecord] = []

    def persist(
        self, *, context: RunPageInspectionContext, record: PageInspectionRecord
    ) -> tuple[str, bool]:
        assert context.profile is not None
        self.records.append(record)
        return "pgi_test", True


def _context(
    text: str, *, profile: SourceAnalysisProfile | None = None
) -> RunPageInspectionContext:
    used_profile = _profile() if profile is None else profile
    document = InspectionDocument(
        pub_id=_DOCUMENT_PUB,
        url="https://news.example.com/a",
        host="news.example.com",
        extract_status="ok",
        text_cas_key="cas/key",
        text_sha256=sha256(text.encode()).hexdigest(),
        page_title="测试页",
        site_name="测试站",
        publisher="测试站",
        authors=("作者甲",),
        published_at=None,
        published_at_confidence="unknown",
        linked_answers=(),
        repost_members=(
            {
                "source_document_pub_id": _DOCUMENT_PUB,
                "url": "https://news.example.com/a",
                "publisher": "测试站",
                "published_at": None,
            },
        ),
    )
    return RunPageInspectionContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        run_id="00000000-0000-0000-0000-000000000003",
        run_pub_id=_RUN,
        project_pub_id=_PROJECT,
        profile_id="00000000-0000-0000-0000-000000000004",
        profile=used_profile,
        documents=(document,),
        existing_keys=frozenset(),
    )


def test_execute_persists_only_validated_finding_and_attribution() -> None:
    text = "作者账号：安全观察。文章称盛邦安全能力很差，但没有给出测试方法。"
    batch = PageCandidateBatch(
        findings=(_candidate("A1", "statement", [_source_link("盛邦安全能力很差")]),),
        attributions=(
            {
                "kind": "publisher_account",
                "value": "安全观察",
                "quote": "作者账号：安全观察",
                "occurrence": 1,
                "confidence": 0.95,
            },
        ),
    )
    sink = _FakeSink()
    result = execute_page_inspection(
        PageInspectionInput(_TENANT, _PROJECT, _RUN, _PROFILE_PUB),
        enabled=True,
        llm=AuditLlmConfig("key", "model", "https://example.com"),
        loader=_FakeLoader(_context(text)),
        text_store=_FakeTextStore(text),
        sink=sink,
        judge=_FakeJudge(batch),
        max_documents=10,
        max_chars=120_000,
    )
    assert len(result.inspected) == 1
    assert result.invalid_candidates == 0
    record = sink.records[0]
    assert record.status == "completed"
    assert record.findings[0].code == "A1"
    assert record.attribution["publisher_identity"]["account"] == "安全观察"
    assert record.quality["candidate_quotes"] == 1
    assert record.quality["verified_quotes"] == 1
    assert record.quality["quote_hit_rate"] == 1.0


def test_no_profile_is_explicitly_not_requested() -> None:
    context = _context("正文")
    context = RunPageInspectionContext(**{**context.__dict__, "profile_id": None, "profile": None})
    result = execute_page_inspection(
        PageInspectionInput(_TENANT, _PROJECT, _RUN, None),
        enabled=True,
        llm=AuditLlmConfig("", "", ""),
        loader=_FakeLoader(context),
        text_store=_FakeTextStore("正文"),
        sink=_FakeSink(),
        judge=None,
        max_documents=10,
        max_chars=120_000,
    )
    assert result.skipped == "profile_missing"
    assert result.inspected == []


def test_unknown_prompt_version_fails_closed_before_analysis() -> None:
    with pytest.raises(ApplicationError, match="prompt version is unsupported"):
        execute_page_inspection(
            PageInspectionInput(
                _TENANT,
                _PROJECT,
                _RUN,
                _PROFILE_PUB,
                prompt_version="page-hazard-evidence-unknown",
            ),
            enabled=True,
            llm=AuditLlmConfig("", "", ""),
            loader=_FakeLoader(_context("正文")),
            text_store=_FakeTextStore("正文"),
            sink=_FakeSink(),
            judge=None,
            max_documents=10,
            max_chars=120_000,
        )
