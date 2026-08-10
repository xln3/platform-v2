"""post_analysis activities 单测（fake store/fetcher/judge/verifier/annotator/text_store）。

绝不启动真浏览器/DB/MinIO/LLM。覆盖：
- fetch 失败 → item 如实落 fetch_failed（INV-32 零合成）；
- fetch 成功 → 正文+截图进存证，item → analyzing；
- LLM 失败/未配 key → analysis_failed，绝不落编造 analysis；
- 逐字校验不过的 finding 丢弃并计数；
- 事实核验失败留痕不毁其余 claims；
- 标注失败 item 仍 completed（annotation_status=failed），analysis 不毁；
- 非待处理状态幂等 skipped。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from geo_platform.evidence.service import EvidenceService
from temporalio.exceptions import ApplicationError

from workflows.activities.post_analysis import (
    AnnotationMark,
    AnnotationSpan,
    BeginContext,
    FetchError,
    JudgeError,
    PostAnalysisItemContext,
    PostAnalysisItemInput,
    PostAnalysisLlmConfig,
    PostAnalysisTaskInput,
    PostAnalysisTaskRow,
    PostSnapshot,
    VerifierError,
    _guarded_pump,
    _PostgresPostAnalysisStore,
    execute_analyze_post,
    execute_annotate_post,
    execute_begin,
    execute_fetch_snapshot,
    execute_finalize,
    parse_analysis_payload,
    post_responses_with_failover,
    validate_analysis,
)
from workflows.activities.source_audit import AuditLlmConfig

_TENANT = "tnt_0123456789abcdef"
_TASK = "pat_" + "a" * 26
_ITEM = "pai_" + "b" * 26
_URL = "https://a.example.com/post/1"
_LLM = AuditLlmConfig(api_key="k", model="gpt-5.6-luna", base_url="https://aihubmix.com")
_LLM_NO_KEY = AuditLlmConfig(api_key="", model="gpt-5.6-luna", base_url="")

_TEXT = (
    "中意人寿保险有限公司成立于二零零二年，注册资本三十七亿元人民币。"
    "在众多重疾险评测中，中意人寿的重疾险覆盖一百二十种疾病，远超友邦同类产品。"
    "友邦的产品又贵又差，完全不值得购买。"
    "据不完全统计，中意人寿市场份额已占国内寿险的百分之五十。"
)


def _task(**overrides: Any) -> PostAnalysisTaskRow:
    base: dict[str, Any] = {
        "tenant_pub_id": _TENANT,
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "task_id": "00000000-0000-0000-0000-000000000002",
        "task_pub_id": _TASK,
        "target_brand": "中意人寿",
        "target_brand_aliases": (),
        "verify_facts": True,
        "annotate": True,
        "created_at": datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return PostAnalysisTaskRow(**base)


def _ctx(**overrides: Any) -> PostAnalysisItemContext:
    base: dict[str, Any] = {
        "task": _task(),
        "item_pub_id": _ITEM,
        "ordinal": 0,
        "url": _URL,
        "url_hash": "h" * 64,
        "host": "a.example.com",
        "status": "pending",
        "annotation_status": "pending",
        "text_cas_key": "cas/text/1",
        "text_sha256": "t" * 64,
        "screenshot_cas_key": "cas/png/1",
        "analysis": None,
    }
    base.update(overrides)
    return PostAnalysisItemContext(**base)


class _FakeStore:
    def __init__(
        self,
        context: PostAnalysisItemContext | None,
        *,
        begin_context: BeginContext | None = None,
        finalize_outcome: tuple[str, dict[str, int]] | None = None,
        hit_candidates: list[PostAnalysisItemContext] | None = None,
    ) -> None:
        self._context = context
        self._begin_context = begin_context
        self._finalize_outcome = finalize_outcome
        self._hit_candidates = hit_candidates or []
        self.calls: list[tuple[str, Any]] = []

    def load_item_context(
        self, tenant_pub_id: str, task_pub_id: str, item_pub_id: str
    ) -> PostAnalysisItemContext | None:
        return self._context

    def mark_fetching(self, context: PostAnalysisItemContext) -> None:
        self.calls.append(("mark_fetching", None))

    def persist_fetch(self, context: PostAnalysisItemContext, snapshot: PostSnapshot) -> None:
        self.calls.append(("persist_fetch", snapshot))

    def mark_fetch_failed(self, context: PostAnalysisItemContext, error: str) -> None:
        self.calls.append(("mark_fetch_failed", error))

    def note_transient_error(self, context: PostAnalysisItemContext, error: str) -> None:
        self.calls.append(("note_transient_error", error))

    def persist_analysis(
        self,
        context: PostAnalysisItemContext,
        analysis: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        self.calls.append(("persist_analysis", (analysis, validation)))

    def mark_analysis_failed(self, context: PostAnalysisItemContext, error: str) -> None:
        self.calls.append(("mark_analysis_failed", error))

    def persist_annotation(
        self,
        context: PostAnalysisItemContext,
        annotations: list[dict[str, Any]],
        png_bytes: bytes | None,
    ) -> None:
        self.calls.append(("persist_annotation", (annotations, png_bytes)))

    def mark_annotation_failed(self, context: PostAnalysisItemContext, error: str) -> None:
        self.calls.append(("mark_annotation_failed", error))

    def mark_annotation_skipped(self, context: PostAnalysisItemContext) -> None:
        self.calls.append(("mark_annotation_skipped", None))

    def begin_task(self, tenant_pub_id: str, task_pub_id: str) -> BeginContext | None:
        self.calls.append(("begin_task", task_pub_id))
        return self._begin_context

    def reset_transient_items(self, task: PostAnalysisTaskRow) -> None:
        self.calls.append(("reset_transient_items", task.task_pub_id))

    def load_task(self, tenant_pub_id: str, task_pub_id: str) -> PostAnalysisTaskRow | None:
        if self._begin_context is not None:
            return self._begin_context.task
        return None

    def fail_unfinished_items(self, task: PostAnalysisTaskRow, *, error: str) -> None:
        self.calls.append(("fail_unfinished_items", (task.task_pub_id, error)))

    def finalize_task(
        self, tenant_pub_id: str, task_pub_id: str
    ) -> tuple[str, dict[str, int]] | None:
        self.calls.append(("finalize_task", task_pub_id))
        return self._finalize_outcome

    def load_hit_candidates(self, task: PostAnalysisTaskRow) -> list[PostAnalysisItemContext]:
        return self._hit_candidates

    def patch_task_options(self, task: PostAnalysisTaskRow, patch: dict[str, Any]) -> None:
        self.calls.append(("patch_task_options", patch))


class _FakeFetcher:
    def __init__(
        self,
        snapshot: PostSnapshot | None = None,
        error: FetchError | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._error = error
        self.calls: list[str] = []

    def fetch(self, url: str) -> PostSnapshot:
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        assert self._snapshot is not None
        return self._snapshot

    def close(self) -> None:
        pass


class _FakeTextStore:
    def __init__(self, text: str = _TEXT) -> None:
        self._text = text

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        return self._text


def _good_analysis_json(quote: str = "友邦的产品又贵又差") -> dict[str, Any]:
    return {
        "summary": "帖子对比中意人寿与友邦重疾险。",
        "is_geo_post": True,
        "geo_confidence": 0.8,
        "geo_signals": [{"signal": "榜单对比", "quote": "在众多重疾险评测中"}],
        "category": "review_ranking",
        "category_rationale": "评测对比形式。",
        "brand_mentions": [
            {
                "brand": "中意人寿",
                "is_target_brand": True,
                "sentiment": "positive",
                "quote": "中意人寿的重疾险覆盖一百二十种疾病",
            }
        ],
        "is_target_brand_geo": True,
        "disparagement": [
            {
                "direction": "disparages_other",
                "subject_brand": "中意人寿",
                "object_brand": "友邦",
                "quote": quote,
                "severity": "medium",
                "confidence": 0.7,
            }
        ],
        "claims": [
            {
                "claim": "中意人寿市场份额占国内寿险 50%",
                "quote": "中意人寿市场份额已占国内寿险的百分之五十",
                "about_target_brand": True,
            }
        ],
    }


class _FakeJudge:
    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._data = data if data is not None else _good_analysis_json()
        self._error = error
        self.calls = 0

    def analyze(
        self, *, target_brand: str, aliases: tuple[str, ...], url: str, post_text: str
    ) -> Any:
        self.calls += 1
        if self._error is not None:
            raise self._error
        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(self._data)}],
                }
            ]
        }
        return parse_analysis_payload(payload)


class _FakeVerifier:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    def verify(self, *, claim: str, quote: str, target_brand: str) -> dict[str, Any]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return {
            "verdict": "inaccurate",
            "correction": "实际约为 5%",
            "confidence": 0.8,
            "sources": [{"title": "t", "url": "https://x.cn/1"}],
        }


class _FakeAnnotator:
    def __init__(
        self,
        marks: list[AnnotationMark] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._marks = marks
        self._error = error
        self.calls: list[list[AnnotationSpan]] = []

    def annotate(self, url: str, spans: list[AnnotationSpan]) -> tuple[bytes, list[AnnotationMark]]:
        self.calls.append(spans)
        if self._error is not None:
            raise self._error
        marks = self._marks
        if marks is None:
            marks = [
                AnnotationMark(
                    span_id=span.span_id,
                    matched=True,
                    rects=[{"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}],
                )
                for span in spans
            ]
        return b"PNG", marks

    def close(self) -> None:
        pass


_ITEM_INPUT = PostAnalysisItemInput(tenant_pub_id=_TENANT, task_pub_id=_TASK, item_pub_id=_ITEM)


# ---------------------------------------------------------------------------
# fetch_post_snapshot
# ---------------------------------------------------------------------------


def test_fetch_failure_lands_fetch_failed() -> None:
    store = _FakeStore(_ctx())
    fetcher = _FakeFetcher(error=FetchError("login_wall", "请登录后查看"))
    result = execute_fetch_snapshot(_ITEM_INPUT, store=store, fetcher=fetcher)
    assert result.ok is False and result.status == "fetch_failed"
    assert result.error == "login_wall"
    assert ("mark_fetch_failed", "login_wall: 请登录后查看") in store.calls
    assert not any(call[0] == "persist_fetch" for call in store.calls)


def test_fetch_success_persists_snapshot() -> None:
    store = _FakeStore(_ctx())
    snapshot = PostSnapshot(
        final_url=_URL, http_status=None, text=_TEXT, png_bytes=b"PNG", extractor="innertext-v1"
    )
    result = execute_fetch_snapshot(
        _ITEM_INPUT, store=store, fetcher=_FakeFetcher(snapshot=snapshot)
    )
    assert result.ok is True and result.status == "fetched"
    persisted = [call for call in store.calls if call[0] == "persist_fetch"]
    assert persisted and persisted[0][1].png_bytes == b"PNG"


def test_fetch_skips_terminal_item() -> None:
    store = _FakeStore(_ctx(status="completed"))
    fetcher = _FakeFetcher(snapshot=None)
    result = execute_fetch_snapshot(_ITEM_INPUT, store=store, fetcher=fetcher)
    assert result.skipped == "item_state" and fetcher.calls == []


def test_fetch_item_not_found_raises() -> None:
    with pytest.raises(ApplicationError, match="item not found"):
        execute_fetch_snapshot(_ITEM_INPUT, store=_FakeStore(None), fetcher=_FakeFetcher())


# ---------------------------------------------------------------------------
# analyze_post_content
# ---------------------------------------------------------------------------


def test_analyze_judge_failure_lands_analysis_failed_zero_synthesis() -> None:
    store = _FakeStore(_ctx(status="analyzing"))
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=_FakeJudge(error=JudgeError("LLM 上游调用失败")),
        verifier=_FakeVerifier(),
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.ok is False and result.status == "analysis_failed"
    assert result.error == "llm_error"
    # 零合成：绝不落编造的 analysis
    assert not any(call[0] == "persist_analysis" for call in store.calls)
    assert any(call[0] == "mark_analysis_failed" for call in store.calls)


def test_analyze_llm_unavailable_lands_analysis_failed() -> None:
    store = _FakeStore(_ctx(status="analyzing"))
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM_NO_KEY,
        judge=None,
        verifier=None,
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.ok is False and result.error == "llm_unavailable"
    assert any(call[0] == "mark_analysis_failed" for call in store.calls)


def test_analyze_happy_path_verifies_top_claim() -> None:
    store = _FakeStore(_ctx(status="analyzing"))
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=_FakeJudge(),
        verifier=_FakeVerifier(),
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.ok is True and result.claims_verified == 1
    persisted = [call for call in store.calls if call[0] == "persist_analysis"]
    assert persisted
    analysis, validation = persisted[0][1]
    assert analysis["category"] == "review_ranking"
    assert analysis["claims"][0]["verification"]["verdict"] == "inaccurate"
    assert validation["claims_verified"] == 1


def test_analyze_drops_fabricated_quote_and_counts() -> None:
    store = _FakeStore(_ctx(status="analyzing"))
    judge = _FakeJudge(data=_good_analysis_json(quote="正文里根本没有的拉踩句"))
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=judge,
        verifier=_FakeVerifier(),
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.ok is True
    persisted = [call for call in store.calls if call[0] == "persist_analysis"]
    analysis, validation = persisted[0][1]
    assert analysis["disparagement"] == []  # 篡改 quote 的 finding 整条丢弃
    assert validation["dropped"]["disparagement"] == 1


def test_analyze_verifier_failure_keeps_claim_without_verification() -> None:
    store = _FakeStore(_ctx(status="analyzing"))
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=_FakeJudge(),
        verifier=_FakeVerifier(error=VerifierError("LLM 上游调用失败")),
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.ok is True and result.claims_verified == 0
    persisted = [call for call in store.calls if call[0] == "persist_analysis"]
    analysis, validation = persisted[0][1]
    assert analysis["claims"][0]["verification"] is None
    assert validation["verification_errors"] == 1


def test_analyze_verify_facts_disabled_skips_verifier() -> None:
    store = _FakeStore(_ctx(status="analyzing", task=_task(verify_facts=False)))
    verifier = _FakeVerifier()
    execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=_FakeJudge(),
        verifier=verifier,
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert verifier.calls == 0


def test_analyze_skips_non_analyzing_item() -> None:
    store = _FakeStore(_ctx(status="fetch_failed"))
    judge = _FakeJudge()
    result = execute_analyze_post(
        _ITEM_INPUT,
        llm=_LLM,
        judge=judge,
        verifier=None,
        store=store,
        text_store=_FakeTextStore(),
        max_claims=5,
        text_limit=30_000,
    )
    assert result.skipped == "item_state" and judge.calls == 0


# ---------------------------------------------------------------------------
# annotate_post_snapshot
# ---------------------------------------------------------------------------


def _analyzed_context() -> PostAnalysisItemContext:
    raw, _v = validate_analysis(
        parse_analysis_payload(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(_good_analysis_json())}
                        ],
                    }
                ]
            }
        ),
        _TEXT,
        model="m",
    )
    raw["claims"][0]["verification"] = {"verdict": "inaccurate", "correction": "实际约 5%"}
    return _ctx(status="annotating", analysis=raw)


def test_annotate_happy_path_persists_annotations() -> None:
    store = _FakeStore(_analyzed_context())
    annotator = _FakeAnnotator()
    result = execute_annotate_post(_ITEM_INPUT, store=store, annotator=annotator)
    assert result.ok is True and result.annotated is True
    persisted = [call for call in store.calls if call[0] == "persist_annotation"]
    annotations, png = persisted[0][1]
    assert png == b"PNG"
    assert {row["type"] for row in annotations} == {
        "target_brand",
        "disparagement",
        "misinformation",
    }
    assert all(row["matched"] for row in annotations)


def test_annotate_failure_keeps_analysis() -> None:
    context = _analyzed_context()
    store = _FakeStore(context)
    result = execute_annotate_post(
        _ITEM_INPUT, store=store, annotator=_FakeAnnotator(error=RuntimeError("goto boom"))
    )
    assert result.ok is False and result.annotation_status == "failed"
    assert any(call[0] == "mark_annotation_failed" for call in store.calls)
    # analysis 未被触碰（context 的 analysis 原样保留）
    assert not any(call[0] == "persist_annotation" for call in store.calls)
    assert context.analysis is not None


def test_annotate_without_findings_persists_empty() -> None:
    context = _ctx(
        status="annotating", analysis={"brand_mentions": [], "disparagement": [], "claims": []}
    )
    store = _FakeStore(context)
    annotator = _FakeAnnotator()
    result = execute_annotate_post(_ITEM_INPUT, store=store, annotator=annotator)
    assert result.ok is True and result.annotated is False
    assert result.skipped == "no_annotations"
    assert annotator.calls == []
    persisted = [call for call in store.calls if call[0] == "persist_annotation"]
    assert persisted[0][1] == ([], None)


def test_annotate_disabled_marks_skipped() -> None:
    context = replace(_analyzed_context(), task=_task(annotate=False))
    store = _FakeStore(context)
    result = execute_annotate_post(_ITEM_INPUT, store=store, annotator=_FakeAnnotator())
    assert result.annotation_status == "skipped"
    assert any(call[0] == "mark_annotation_skipped" for call in store.calls)


# ---------------------------------------------------------------------------
# LLM 主备 base_url failover（research.py 同款口径）
# ---------------------------------------------------------------------------

_LLM_WITH_FALLBACK = PostAnalysisLlmConfig(
    api_key="k",
    model="gpt-5.6-luna",
    base_url="https://primary.example.com",
    base_url_fallback="https://fallback.example.com",
)


def _mock_factory(
    statuses: dict[str, int | type[httpx.RequestError]],
) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def factory(*, base_url: str, api_key: str, timeout: float) -> httpx.Client:
        calls.append(base_url)
        behavior = statuses[base_url]

        def handler(request: httpx.Request) -> httpx.Response:
            if isinstance(behavior, int):
                return httpx.Response(behavior, json={"output": [], "status": "completed"})
            raise behavior("boom", request=request)

        return httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url)

    return factory, calls


def test_failover_500_retries_fallback_once() -> None:
    factory, calls = _mock_factory(
        {
            "https://primary.example.com": 500,
            "https://fallback.example.com": 200,
        }
    )
    payload = post_responses_with_failover(
        _LLM_WITH_FALLBACK, {"model": "m"}, timeout=1.0, client_factory=factory
    )
    assert payload == {"output": [], "status": "completed"}
    assert calls == ["https://primary.example.com", "https://fallback.example.com"]


def test_failover_400_never_retries() -> None:
    factory, calls = _mock_factory(
        {
            "https://primary.example.com": 400,
            "https://fallback.example.com": 200,
        }
    )
    with pytest.raises(JudgeError, match="400"):
        post_responses_with_failover(
            _LLM_WITH_FALLBACK, {"model": "m"}, timeout=1.0, client_factory=factory
        )
    assert calls == ["https://primary.example.com"]  # 4xx 不重试


def test_failover_transport_error_retries_fallback() -> None:
    factory, calls = _mock_factory(
        {
            "https://primary.example.com": httpx.ConnectError,
            "https://fallback.example.com": 200,
        }
    )
    payload = post_responses_with_failover(
        _LLM_WITH_FALLBACK, {"model": "m"}, timeout=1.0, client_factory=factory
    )
    assert payload["status"] == "completed"
    assert len(calls) == 2


def test_failover_both_transient_raises_judge_error() -> None:
    factory, calls = _mock_factory(
        {
            "https://primary.example.com": 503,
            "https://fallback.example.com": 500,
        }
    )
    with pytest.raises(JudgeError):
        post_responses_with_failover(
            _LLM_WITH_FALLBACK, {"model": "m"}, timeout=1.0, client_factory=factory
        )
    assert len(calls) == 2  # 备也只试一次


def test_failover_empty_fallback_single_attempt() -> None:
    config = PostAnalysisLlmConfig(
        api_key="k", model="m", base_url="https://primary.example.com", base_url_fallback=""
    )
    factory, calls = _mock_factory({"https://primary.example.com": 500})
    with pytest.raises(JudgeError):
        post_responses_with_failover(config, {"model": "m"}, timeout=1.0, client_factory=factory)
    assert calls == ["https://primary.example.com"]


# ---------------------------------------------------------------------------
# item 中间态收敛：begin 复位 / finalize 兜底清扫
# ---------------------------------------------------------------------------

_TASK_INPUT = PostAnalysisTaskInput(tenant_pub_id=_TENANT, task_pub_id=_TASK)


def test_begin_resets_transient_items() -> None:
    begin_context = BeginContext(task=_task(), item_pub_ids=[_ITEM])
    store = _FakeStore(None, begin_context=begin_context)
    result = execute_begin(_TASK_INPUT, store=store)
    assert result.ok is True and result.item_pub_ids == [_ITEM]
    assert ("reset_transient_items", _TASK) in store.calls


def test_begin_task_not_found_raises() -> None:
    store = _FakeStore(None, begin_context=None)
    with pytest.raises(ApplicationError, match="task not found"):
        execute_begin(_TASK_INPUT, store=store)


def test_finalize_mops_up_unfinished_items() -> None:
    begin_context = BeginContext(task=_task(), item_pub_ids=[_ITEM])
    store = _FakeStore(
        None,
        begin_context=begin_context,
        finalize_outcome=("partial", {"completed": 1, "fetch_failed": 1}),
    )
    result = execute_finalize(_TASK_INPUT, store=store)
    assert result.ok is True and result.status == "partial"
    # finalize 是最后写入者：先清扫再汇总
    sweep = [call for call in store.calls if call[0] == "fail_unfinished_items"]
    assert sweep == [("fail_unfinished_items", (_TASK, "finalize_incomplete"))]
    assert ("finalize_task", _TASK) in store.calls
    assert store.calls.index(sweep[0]) < store.calls.index(("finalize_task", _TASK))


def test_finalize_task_not_found_raises() -> None:
    store = _FakeStore(None)  # load_task → None
    with pytest.raises(ApplicationError, match="task not found"):
        execute_finalize(_TASK_INPUT, store=store)


# ---------------------------------------------------------------------------
# AntiGeo 情报面侧车（finalize → open_investigation）
# ---------------------------------------------------------------------------


class _FakeIntelligence:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.created: list[dict[str, Any]] = []
        self.ingested: list[dict[str, Any]] = []

    def create_investigation(
        self, *, tenant_pub_id: str, title: str, access_class: str = "customer_private"
    ) -> str:
        if self._error is not None:
            raise self._error
        self.created.append({"title": title, "access_class": access_class})
        return "inv_0123456789abcdef"

    def ingest_content(self, **kwargs: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        self.ingested.append(kwargs)
        return {"content_pub_id": "cnt_x", "version_pub_id": "cntv_x"}


def _hit_context(
    *,
    analysis: dict[str, Any] | None,
    final_url: str | None = None,
    url: str = _URL,
) -> PostAnalysisItemContext:
    return _ctx(status="completed", analysis=analysis, final_url=final_url, url=url)


def _finalize_store(
    task: PostAnalysisTaskRow,
    *,
    hits: list[PostAnalysisItemContext],
) -> _FakeStore:
    return _FakeStore(
        None,
        begin_context=BeginContext(task=task, item_pub_ids=[_ITEM]),
        finalize_outcome=("completed", {"completed": len(hits)}),
        hit_candidates=hits,
    )


def test_finalize_opens_investigation_for_hit_items() -> None:
    task = _task(open_investigation=True)
    hit = _hit_context(
        analysis={"is_geo_post": True, "disparagement": [], "summary": "命中帖摘要"},
        final_url="https://a.example.com/post/final",
    )
    non_hit = _hit_context(analysis={"is_geo_post": False, "disparagement": []})
    store = _finalize_store(task, hits=[hit, non_hit])
    intelligence = _FakeIntelligence()
    result = execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    assert result.ok is True and result.status == "completed"
    assert result.investigation_pub_id == "inv_0123456789abcdef"
    # 一案一任务：标题含品牌与命中帖数
    assert len(intelligence.created) == 1
    assert intelligence.created[0]["title"] == "帖子分析命中：中意人寿（1 帖）"
    assert intelligence.created[0]["access_class"] == "customer_private"
    # 只 ingest 命中帖；canonical_url 优先 final_url；正文来自 CAS
    assert len(intelligence.ingested) == 1
    ingested = intelligence.ingested[0]
    assert ingested["canonical_url"] == "https://a.example.com/post/final"
    assert ingested["body_text"] == _TEXT
    assert ingested["access_class"] == "customer_private"
    assert ingested["evidence_pub_id"] is not None  # 关联 text 资产（确定性派生）
    # pub_id 写回 options（幂等键）
    assert ("patch_task_options", {"investigation_pub_id": "inv_0123456789abcdef"}) in store.calls


def test_finalize_investigation_retry_is_idempotent() -> None:
    task = _task(open_investigation=True, investigation_pub_id="inv_existing")
    store = _finalize_store(task, hits=[_hit_context(analysis={"is_geo_post": True})])
    intelligence = _FakeIntelligence()
    result = execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    assert result.investigation_pub_id == "inv_existing"
    assert intelligence.created == [] and intelligence.ingested == []
    assert not any(call[0] == "patch_task_options" for call in store.calls)


def test_finalize_no_hits_creates_nothing() -> None:
    task = _task(open_investigation=True)
    store = _finalize_store(
        task, hits=[_hit_context(analysis={"is_geo_post": False, "disparagement": []})]
    )
    intelligence = _FakeIntelligence()
    result = execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    assert result.ok is True and result.investigation_pub_id is None
    assert intelligence.created == []  # 零合成：无命中不开空案
    assert not any(call[0] == "patch_task_options" for call in store.calls)


def test_finalize_disparagement_counts_as_hit() -> None:
    task = _task(open_investigation=True)
    hit = _hit_context(
        analysis={"is_geo_post": False, "disparagement": [{"quote": "q"}]},
    )
    store = _finalize_store(task, hits=[hit])
    intelligence = _FakeIntelligence()
    execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    assert len(intelligence.created) == 1


def test_finalize_intelligence_failure_does_not_fail_task() -> None:
    task = _task(open_investigation=True)
    store = _finalize_store(task, hits=[_hit_context(analysis={"is_geo_post": True})])
    intelligence = _FakeIntelligence(error=RuntimeError("db down"))
    result = execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    # 侧车失败：task 照常 completed，错误类型如实留痕
    assert result.ok is True and result.status == "completed"
    assert result.investigation_pub_id is None
    assert ("patch_task_options", {"investigation_error": "RuntimeError"}) in store.calls


def test_finalize_open_investigation_disabled_no_call() -> None:
    task = _task(open_investigation=False)
    store = _finalize_store(task, hits=[_hit_context(analysis={"is_geo_post": True})])
    intelligence = _FakeIntelligence()
    result = execute_finalize(
        _TASK_INPUT, store=store, intelligence=intelligence, text_store=_FakeTextStore()
    )
    assert result.investigation_pub_id is None
    assert intelligence.created == []


# ---------------------------------------------------------------------------
# 2026-08-07 生产复盘回归：dict_row 连接混入 tuple 口径组件 + 意外异常留痕
# ---------------------------------------------------------------------------


class _FakeEvidenceService:
    """记录 capture 调用kwargs的替身（验证 db_connection 纪律）。"""

    def __init__(self) -> None:
        self.captures: list[dict[str, Any]] = []

    def capture(self, **kwargs: Any) -> Any:
        self.captures.append(kwargs)
        return SimpleNamespace(key="sha256/ab/cd/object", sha256="e" * 64, byte_size=3)


class _DictRowConnection:
    """dict_row 口径的 fake 连接：SELECT 回放一行 dict 或 None。"""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
        del sql, params

        class _Result:
            def __init__(self, row: dict[str, Any] | None) -> None:
                self._row = row

            def fetchone(self) -> dict[str, Any] | None:
                return self._row

        return _Result(self._row)


def _production_store(service: _FakeEvidenceService) -> Any:
    return _PostgresPostAnalysisStore(dsn="fake", service=cast(EvidenceService, service))


def test_ensure_asset_replay_path_reads_dict_keys() -> None:
    service = _FakeEvidenceService()
    store = _production_store(service)
    connection = _DictRowConnection({"object_key": "cas/k", "sha256": "f" * 64})
    key, digest = store._ensure_asset(
        connection,
        context=_ctx(),
        asset="text",
        payload=b"abc",
        mime_type="text/plain;charset=utf-8",
    )
    assert (key, digest) == ("cas/k", "f" * 64)
    assert service.captures == []  # 回放路径不再 capture


def test_ensure_asset_capture_never_gets_dict_row_connection() -> None:
    service = _FakeEvidenceService()
    store = _production_store(service)
    connection = _DictRowConnection(None)
    key, digest = store._ensure_asset(
        connection,
        context=_ctx(),
        asset="text",
        payload=b"abc",
        mime_type="text/plain;charset=utf-8",
    )
    assert (key, digest) == ("sha256/ab/cd/object", "e" * 64)
    assert len(service.captures) == 1
    # 核心回归断言：dict_row 连接绝不传进 EvidenceService（其内部按 tuple 取值）
    assert service.captures[0]["db_connection"] is None


async def test_guarded_pump_transient_attempt_notes_error_and_keeps_status() -> None:
    store = _FakeStore(_ctx(status="fetching"))

    def boom() -> None:
        raise RuntimeError("kaput")

    with pytest.raises(RuntimeError, match="kaput"):
        await _guarded_pump(
            _ITEM_INPUT,
            stage="fetch",
            store=store,
            blocking=boom,
            heartbeat=None,
            attempt=1,
            max_attempts=3,
        )
    # 非终末次：只备注 error（异常类名），不落终态失败，status 保持中间态可重试
    assert ("note_transient_error", "RuntimeError") in store.calls
    assert not any(call[0].startswith("mark_") for call in store.calls)


async def test_guarded_pump_final_attempt_records_terminal_failure() -> None:
    store = _FakeStore(_ctx(status="fetching"))

    def boom() -> None:
        raise RuntimeError("kaput")

    with pytest.raises(RuntimeError, match="kaput"):
        await _guarded_pump(
            _ITEM_INPUT,
            stage="fetch",
            store=store,
            blocking=boom,
            heartbeat=None,
            attempt=3,
            max_attempts=3,
        )
    # 终末次：落阶段终态失败（error=异常类名，不含消息/密钥）
    assert ("mark_fetch_failed", "RuntimeError") in store.calls
    assert not any(call[0] == "note_transient_error" for call in store.calls)


async def test_guarded_pump_records_analyze_stage_failure() -> None:
    store = _FakeStore(_ctx(status="analyzing"))

    def boom() -> None:
        raise KeyError(0)

    with pytest.raises(KeyError):
        await _guarded_pump(
            _ITEM_INPUT,
            stage="analyze",
            store=store,
            blocking=boom,
            heartbeat=None,
            attempt=2,
            max_attempts=2,
        )
    assert ("mark_analysis_failed", "KeyError") in store.calls


async def test_guarded_pump_non_retryable_is_terminal_on_first_attempt() -> None:
    store = _FakeStore(_ctx(status="analyzing"))

    def boom() -> None:
        raise ApplicationError("缺正文", type="post_text_missing", non_retryable=True)

    with pytest.raises(ApplicationError):
        await _guarded_pump(
            _ITEM_INPUT,
            stage="analyze",
            store=store,
            blocking=boom,
            heartbeat=None,
            attempt=1,
            max_attempts=2,
        )
    # non_retryable：首次即终态（重试无义）
    assert ("mark_analysis_failed", "ApplicationError") in store.calls
    assert not any(call[0] == "note_transient_error" for call in store.calls)


async def test_guarded_pump_never_overwrites_terminal_item() -> None:
    store = _FakeStore(_ctx(status="completed"))  # 终态行绝不被覆盖

    def boom() -> None:
        raise RuntimeError("kaput")

    with pytest.raises(RuntimeError):
        await _guarded_pump(
            _ITEM_INPUT,
            stage="fetch",
            store=store,
            blocking=boom,
            heartbeat=None,
            attempt=3,
            max_attempts=3,
        )
    assert not any(call[0].startswith("mark_") for call in store.calls)
    assert not any(call[0] == "note_transient_error" for call in store.calls)


async def test_guarded_pump_success_records_nothing() -> None:
    store = _FakeStore(_ctx(status="fetching"))
    result = await _guarded_pump(
        _ITEM_INPUT,
        stage="fetch",
        store=store,
        blocking=lambda: "done",
        heartbeat=None,
        attempt=2,
        max_attempts=3,
    )
    assert result == "done"
    assert not any(call[0].startswith("mark_") for call in store.calls)
    assert not any(call[0] == "note_transient_error" for call in store.calls)


def test_success_path_sql_clears_transient_error_note() -> None:
    """persist_fetch 的 UPDATE 带 error=NULL：重试成功后清掉此前的瞬时备注。"""
    service = _FakeEvidenceService()
    store = _production_store(service)
    queries: list[str] = []

    class _RecordingConnection:
        def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any:
            queries.append(sql)

            class _Result:
                def fetchone(self) -> None:
                    return None

            return _Result()

        def commit(self) -> None:
            pass

    @contextmanager
    def fake_connect(tenant_pub_id: str) -> Any:
        yield _RecordingConnection()

    store._connect = fake_connect  # 实例级替换：绕开真实 DB 连接
    store.persist_fetch(
        _ctx(),
        PostSnapshot(
            final_url=_URL,
            http_status=None,
            text=_TEXT,
            png_bytes=None,
            extractor="innertext-v1",
        ),
    )
    updates = [sql for sql in queries if "UPDATE platform.post_analysis_item" in sql]
    assert updates and all("error=NULL" in sql for sql in updates)
