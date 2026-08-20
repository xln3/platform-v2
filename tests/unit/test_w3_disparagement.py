"""W3 拉踩检测单元测试（domain 纯函数 + judge_run_disparagement 主流程）。

切窗（提及命中/竞品共现/窗边界）、verbatim 正反例、词典兜底标 experimental、
validate_judgment 语义规则、execute 主流程（LLM ok / validation_failure 丢弃 /
llm_unavailable→词典 / JudgeError→词典+failures / 幂等跳过 / 窗数上限截断 /
disabled / CAS 失败）全部依赖注入 fake，绝不打真 LLM/DB/MinIO。
"""

from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError

from domain.scoring.disparagement import (
    DICTIONARY_VERSION,
    METHOD_DICTIONARY,
    METHOD_LLM,
    PROMPT_VERSION,
    WINDOW_RADIUS,
    Window,
    clamp_window_limit,
    dedupe_windows,
    dictionary_judge,
    expand_table_fragment_quote,
    extract_windows,
    quote_is_verbatim,
    validate_judgment,
    window_text_hash,
)
from workflows.activities.disparagement import (
    AnswerSubject,
    DisparagementInput,
    DisparagementRecord,
    DisparagementResult,
    DocumentSubject,
    JudgeError,
    LlmJudgment,
    RunDisparagementContext,
    derive_judgment_pub_id,
    execute_disparagement,
)
from workflows.activities.source_audit import AuditLlmConfig

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_RUN = "run_0123456789abcdef"

_ITEM = DisparagementInput(tenant_pub_id=_TENANT, project_pub_id=_PROJECT, run_pub_id=_RUN)
_LLM = AuditLlmConfig(api_key="k", model="gpt-5.6-luna", base_url="https://aihubmix.com")
_LLM_NO_KEY = AuditLlmConfig(api_key="", model="gpt-5.6-luna", base_url="https://aihubmix.com")

_BRAND = "中意人寿"
_COMPETITORS = ("友邦", "平安")

_ANSWER_TEXT = (
    "在选择寿险时需要综合比较。友邦的重疾险价格明显偏贵，保障范围也不如中意人寿全面，"
    "性价比堪忧。中意人寿的重疾险覆盖一百二十种疾病，含轻症豁免，值得推荐。"
    "平安的理赔流程也被部分用户吐槽繁琐。"
)
_DOC_TEXT = (
    "行业观察：中意人寿的服务口碑明显不如友邦，平安代理人渠道投诉量也居高不下。"
    "友邦以高品质代理人团队著称，长期口碑不错。"
)


def _window(text: str, target: str) -> Window:
    return Window(
        subject_type="answer",
        subject_pub_id="ans_x",
        platform="doubao",
        source_url="",
        target_brand=target,
        kind="mention",
        text=text,
        window_hash=window_text_hash(text),
    )


# ---------------------------------------------------------------------------
# 切窗
# ---------------------------------------------------------------------------


def test_extract_windows_mention_hit_radius() -> None:
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=_ANSWER_TEXT,
        brand=_BRAND,
        competitors=_COMPETITORS,
        platform="doubao",
    )
    by_target = {w.target_brand: w for w in windows if w.kind == "mention"}
    assert set(by_target) == {"中意人寿", "友邦", "平安"}
    for window in windows:
        assert window.target_brand in window.text
        assert window.window_hash == window_text_hash(window.text)
        assert window.platform == "doubao"
    # 窗半径：提及距两端都远时窗长 = 品牌长度 + 2*radius
    padded = "前文。" * 300 + "友邦保险。" + "后文。" * 300
    far = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=padded,
        brand=None,
        competitors=("友邦",),
        platform="doubao",
    )
    assert len(far) == 1
    assert len(far[0].text) == len("友邦") + 2 * WINDOW_RADIUS


def test_extract_windows_boundary_clamps_to_text_start() -> None:
    text = "友邦保险成立较早。" + "后续内容。" * 100
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=text,
        brand=None,
        competitors=("友邦",),
        platform="doubao",
    )
    assert len(windows) == 1
    assert windows[0].text.startswith("友邦保险")  # 左边界夹在 0，不出负索引


def test_extract_windows_competitor_pair_window() -> None:
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=_ANSWER_TEXT,
        brand=_BRAND,
        competitors=_COMPETITORS,
        platform="doubao",
    )
    pair_windows = [w for w in windows if w.kind == "competitor_pair"]
    pair_targets = {w.target_brand for w in pair_windows}
    # 友邦与平安在 600 字符内共现 → 合并窗对两个竞品各产一扇
    assert pair_targets == {"友邦", "平安"}
    for window in pair_windows:
        assert "友邦" in window.text and "平安" in window.text


def test_extract_windows_no_pair_when_too_far() -> None:
    text = "友邦" + "间隔" * 400 + "平安"
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=text,
        brand=None,
        competitors=("友邦", "平安"),
        platform="doubao",
    )
    assert [w for w in windows if w.kind == "competitor_pair"] == []


def test_extract_windows_skips_mention_inside_previous_window() -> None:
    text = "友邦" + "短" * 10 + "友邦"  # 第二次提及落在第一扇窗内
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=text,
        brand=None,
        competitors=("友邦",),
        platform="doubao",
    )
    assert len(windows) == 1


def test_extract_windows_empty_text_and_bad_type() -> None:
    assert (
        extract_windows(
            subject_type="answer",
            subject_pub_id="a",
            text="  ",
            brand=_BRAND,
            competitors=_COMPETITORS,
            platform="m",
        )
        == []
    )
    with pytest.raises(ValueError, match="subject_type"):
        extract_windows(
            subject_type="weird",
            subject_pub_id="a",
            text="友邦",
            brand=None,
            competitors=(),
            platform="m",
        )


def test_dedupe_windows_by_subject_hash_target() -> None:
    w1 = _window("友邦价格偏贵。", "友邦")
    w2 = _window("友邦价格偏贵。", "友邦")  # 同窗同 target → 去重
    w3 = _window("友邦价格偏贵。", "平安")  # 同窗不同 target → 保留
    w4 = Window(
        subject_type="source_document",
        subject_pub_id="srd_x",
        platform="example.com",
        source_url="https://example.com/a",
        target_brand="友邦",
        kind="mention",
        text="友邦价格偏贵。",
        window_hash=window_text_hash("友邦价格偏贵。"),
    )  # 同窗文本不同 subject → 保留
    unique = dedupe_windows([w1, w2, w3, w4])
    assert unique == [w1, w3, w4]


# ---------------------------------------------------------------------------
# verbatim 校验
# ---------------------------------------------------------------------------


def test_quote_is_verbatim_positive_and_negative() -> None:
    assert quote_is_verbatim("覆盖一百二十种\n疾病", "覆盖一百二十种 疾病，含轻症豁免")
    assert quote_is_verbatim("价格偏贵", "友邦的重疾险价格偏贵。")
    assert not quote_is_verbatim("", "任何文本")
    assert not quote_is_verbatim("编造的话", "友邦的重疾险价格偏贵。")


def test_validate_judgment_accepts_good_judgment() -> None:
    window = _window(_ANSWER_TEXT, "友邦")
    judgment = LlmJudgment(
        subject="",
        target="友邦",
        attitude="negative",
        disparagement=True,
        evidence_quote="价格明显偏贵，保障范围也不如中意人寿全面",
        confidence=0.9,
    )
    assert (
        validate_judgment(
            judgment,
            window_text=window.text,
            expected_target="友邦",
            known_brands=(_BRAND, *_COMPETITORS),
        )
        is None
    )


def test_validate_judgment_rejects_tampered_quote() -> None:
    judgment = LlmJudgment("", "友邦", "negative", True, "窗里根本没有这句话", 0.9)
    failure = validate_judgment(
        judgment,
        window_text=_ANSWER_TEXT,
        expected_target="友邦",
        known_brands=(_BRAND, *_COMPETITORS),
    )
    assert failure is not None and "逐字" in failure


def test_validate_judgment_rejects_target_mismatch() -> None:
    judgment = LlmJudgment("", "平安", "negative", True, "性价比堪忧", 0.5)
    failure = validate_judgment(
        judgment,
        window_text=_ANSWER_TEXT,
        expected_target="友邦",
        known_brands=(_BRAND, *_COMPETITORS),
    )
    assert failure is not None and "target" in failure


def test_validate_judgment_rejects_unknown_subject() -> None:
    judgment = LlmJudgment("不知名公司", "友邦", "negative", True, "性价比堪忧", 0.5)
    failure = validate_judgment(
        judgment,
        window_text=_ANSWER_TEXT,
        expected_target="友邦",
        known_brands=(_BRAND, *_COMPETITORS),
    )
    assert failure is not None and "subject" in failure


def test_validate_judgment_rejects_contradictory_disparagement() -> None:
    support_flag = LlmJudgment("", "友邦", "support", True, "性价比堪忧", 0.5)
    assert (
        validate_judgment(
            support_flag,
            window_text=_ANSWER_TEXT,
            expected_target="友邦",
            known_brands=(_BRAND, *_COMPETITORS),
        )
        is not None
    )
    self_flag = LlmJudgment("友邦", "友邦", "negative", True, "性价比堪忧", 0.5)
    assert (
        validate_judgment(
            self_flag,
            window_text=_ANSWER_TEXT,
            expected_target="友邦",
            known_brands=(_BRAND, *_COMPETITORS),
        )
        is not None
    )


def test_validate_judgment_rejects_confidence_out_of_range() -> None:
    judgment = LlmJudgment("", "友邦", "negative", True, "性价比堪忧", 1.5)
    assert (
        validate_judgment(
            judgment,
            window_text=_ANSWER_TEXT,
            expected_target="友邦",
            known_brands=(_BRAND, *_COMPETITORS),
        )
        is not None
    )


# ---------------------------------------------------------------------------
# 词典兜底
# ---------------------------------------------------------------------------


def test_dictionary_judge_negative_with_comparison_is_disparagement() -> None:
    outcome = dictionary_judge(
        "友邦价格偏贵，保障不如中意人寿，性价比堪忧。",
        target_brand="友邦",
        known_brands=(_BRAND, *_COMPETITORS),
    )
    assert outcome.attitude == "negative"
    assert outcome.disparagement is True
    assert outcome.evidence_quote  # 命中词逐字必然在窗内
    assert outcome.evidence_quote in "友邦价格偏贵，保障不如中意人寿，性价比堪忧。"
    assert outcome.confidence <= 0.6  # 词典法置信度硬封顶


def test_dictionary_judge_negative_without_comparison_not_disparagement() -> None:
    outcome = dictionary_judge(
        "中意人寿个别产品被投诉，体验失望。",
        target_brand="中意人寿",
        known_brands=(_BRAND, *_COMPETITORS),
    )
    assert outcome.attitude == "negative"
    assert outcome.disparagement is False  # 无比较对象，批评≠拉踩


def test_dictionary_judge_support_and_neutral() -> None:
    support = dictionary_judge(
        "中意人寿保障全面，值得推荐。", target_brand="中意人寿", known_brands=(_BRAND,)
    )
    assert support.attitude == "support" and support.disparagement is False
    neutral = dictionary_judge(
        "中意人寿成立于二零零二年。", target_brand="中意人寿", known_brands=(_BRAND,)
    )
    assert neutral.attitude == "neutral" and neutral.disparagement is False
    assert neutral.evidence_quote == ""


def test_clamp_window_limit() -> None:
    # 20260810 起缺省 50→1000、硬夹上限 200→10000：正式 run 50 窗必然截断
    assert clamp_window_limit(None) == 1000
    assert clamp_window_limit("") == 1000
    assert clamp_window_limit("0") == 1
    assert clamp_window_limit("999") == 999
    assert clamp_window_limit("20000") == 10000
    assert clamp_window_limit("80") == 80
    assert clamp_window_limit("abc") == 1000


def test_expand_table_fragment_quote_expands_to_full_row() -> None:
    window = (
        "各厂商能力对比如下：\n\n"
        "| 厂商 | 双非排查深度 | 应急响应 |\n"
        "| --- | --- | --- |\n"
        "| 盛邦安全 | 弱，覆盖不全 | 7×24 |\n"
        "| 友商甲 | 强 | 7×24 |\n"
    )
    # 单元格碎片 → 扩到整行
    assert (
        expand_table_fragment_quote("弱，覆盖不全", window) == "| 盛邦安全 | 弱，覆盖不全 | 7×24 |"
    )
    # 跨单元格碎片同样扩行
    assert (
        expand_table_fragment_quote("盛邦安全 | 弱", window) == "| 盛邦安全 | 弱，覆盖不全 | 7×24 |"
    )
    # 已是整行 → 原样
    full_row = "| 友商甲 | 强 | 7×24 |"
    assert expand_table_fragment_quote(full_row, window) == full_row


def test_expand_table_fragment_quote_passthrough() -> None:
    window = "盛邦安全的双非排查深度不如友商。\n这是完整段落证据。"
    # 普通段落 quote 不动
    assert (
        expand_table_fragment_quote("盛邦安全的双非排查深度不如友商。", window)
        == "盛邦安全的双非排查深度不如友商。"
    )
    # 空 quote 不动
    assert expand_table_fragment_quote("", window) == ""
    # 跨行 quote 不动
    multi = "盛邦安全的双非排查深度不如友商。\n这是完整段落证据。"
    assert expand_table_fragment_quote(multi, window) == multi


# ---------------------------------------------------------------------------
# execute 主流程（fake 依赖注入）
# ---------------------------------------------------------------------------


def _context(
    *,
    answers: list[AnswerSubject] | None = None,
    documents: list[DocumentSubject] | None = None,
    existing_keys: frozenset[tuple[str, str, str, str, str]] = frozenset(),
) -> RunDisparagementContext:
    return RunDisparagementContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        run_id="00000000-0000-0000-0000-000000000003",
        run_pub_id=_RUN,
        project_pub_id=_PROJECT,
        brand=_BRAND,
        competitors=_COMPETITORS,
        answers=answers
        if answers is not None
        else [AnswerSubject(pub_id="ans_x", text=_ANSWER_TEXT, model="doubao")],
        documents=documents
        if documents is not None
        else [
            DocumentSubject(
                pub_id="srd_x",
                url="https://a.example.com/article",
                host="a.example.com",
                text_cas_key="cas/key/1",
                text_sha256="a" * 64,
            )
        ],
        existing_keys=existing_keys,
    )


class _FakeLoader:
    def __init__(self, context: RunDisparagementContext | None) -> None:
        self._context = context

    def load(
        self, tenant_pub_id: str, run_pub_id: str, project_pub_id: str
    ) -> RunDisparagementContext | None:
        return self._context


class _FakeJudge:
    """quote 缺省回显 target_brand（必然逐字命中窗文本）；传 explicit quote 则原样返回。"""

    def __init__(
        self,
        outcome: LlmJudgment | None = None,
        error: Exception | None = None,
    ) -> None:
        self._outcome = outcome
        self._error = error
        self.calls: list[str] = []

    def judge(
        self, *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
    ) -> LlmJudgment:
        self.calls.append(target_brand)
        if self._error is not None:
            raise self._error
        assert self._outcome is not None
        return LlmJudgment(
            subject=self._outcome.subject,
            target=target_brand,  # fake 回显 target，语义正确
            attitude=self._outcome.attitude,
            disparagement=self._outcome.disparagement,
            evidence_quote=self._outcome.evidence_quote or target_brand,
            confidence=self._outcome.confidence,
        )


class _FakeTextStore:
    def __init__(self, text: str = _DOC_TEXT, error: Exception | None = None) -> None:
        self._text = text
        self._error = error

    def get_text(self, object_key: str, expected_sha256: str) -> str:
        if self._error is not None:
            raise self._error
        return self._text


class _FakeSink:
    def __init__(self) -> None:
        self.records: list[DisparagementRecord] = []

    def persist(self, *, context: RunDisparagementContext, record: DisparagementRecord) -> str:
        self.records.append(record)
        return derive_judgment_pub_id(
            context.tenant_pub_id,
            context.run_pub_id,
            record.subject_pub_id,
            record.window_hash,
            record.target_brand,
            record.model,
            record.prompt_version,
        )


def _execute(
    *,
    context: RunDisparagementContext | None,
    judge: _FakeJudge | None = None,
    llm: AuditLlmConfig = _LLM,
    sink: _FakeSink | None = None,
    text_store: _FakeTextStore | None = None,
    enabled: bool = True,
    window_limit: int = 50,
) -> tuple[DisparagementResult, _FakeSink]:
    used_sink = sink or _FakeSink()
    result = execute_disparagement(
        _ITEM,
        enabled=enabled,
        window_limit=window_limit,
        llm=llm,
        judge=judge if judge is not None else _FakeJudge(_GOOD),
        loader=_FakeLoader(context),
        text_store=text_store or _FakeTextStore(),
        sink=used_sink,
    )
    return result, used_sink


_GOOD = LlmJudgment("", "", "negative", True, "", 0.9)


def test_execute_disabled_zero_io() -> None:
    sink = _FakeSink()
    result = execute_disparagement(
        _ITEM,
        enabled=False,
        window_limit=50,
        llm=_LLM,
        judge=_FakeJudge(_GOOD),
        loader=_FakeLoader(None),
        text_store=_FakeTextStore(),
        sink=sink,
    )
    assert result.disabled is True and sink.records == []


def test_execute_run_not_found_raises() -> None:
    with pytest.raises(ApplicationError, match="run not found"):
        _execute(context=None)


def test_execute_llm_happy_path() -> None:
    # fake quote=target_brand（每扇窗必含 target，逐字校验全过）
    judge = _FakeJudge(LlmJudgment("", "", "negative", True, "", 0.9))
    result, sink = _execute(context=_context(), judge=judge)
    assert result.failures == [] and result.validation_failures == 0
    assert sink.records and result.judged == len(sink.records)
    assert {record.target_brand for record in sink.records} == {_BRAND}
    assert set(judge.calls) == {_BRAND}
    for record in sink.records:
        assert record.judgment_status == "ok"
        assert record.method == METHOD_LLM
        assert record.model == _LLM.model
        assert record.prompt_version == PROMPT_VERSION
        assert record.attitude == "negative"
        assert record.disparagement is True
        assert record.evidence_quote == record.target_brand
    subjects = {(r.subject_type, r.platform) for r in sink.records}
    assert ("answer", "doubao") in subjects
    assert ("source_document", "a.example.com") in subjects


def test_execute_validation_failure_drops_judgment() -> None:
    judge = _FakeJudge(LlmJudgment("", "", "negative", True, "编造的引用", 0.9))
    result, sink = _execute(context=_context(), judge=judge)
    assert result.judged == 0
    assert result.validation_failures == len(sink.records) > 0
    for record in sink.records:
        assert record.judgment_status == "validation_failure"
        assert record.attitude is None and record.disparagement is None
        assert record.evidence_quote == "编造的引用"  # 问题 quote 如实留痕


def test_execute_llm_unavailable_falls_back_to_dictionary() -> None:
    judge = _FakeJudge(_GOOD)
    target_risk_text = "中意人寿价格偏贵，保障不如友邦，性价比堪忧。"
    result, sink = _execute(
        context=_context(),
        judge=judge,
        llm=_LLM_NO_KEY,
        text_store=_FakeTextStore(target_risk_text),
    )
    assert judge.calls == []  # 一次 LLM 都不调
    assert sink.records, "词典兜底必须落行"
    assert result.dictionary_fallback == len(sink.records) == result.judged
    for record in sink.records:
        assert record.method == METHOD_DICTIONARY  # experimental 标法
        assert record.model == ""
        assert record.prompt_version == DICTIONARY_VERSION
        assert record.judgment_status == "ok"
    # 执行层只评价目标品牌；信源正文中的“中意人寿不如友邦”应判 negative+拉踩。
    target_brand_case = next(
        r
        for r in sink.records
        if r.target_brand == _BRAND and r.subject_type == "source_document" and r.disparagement
    )
    assert target_brand_case.attitude == "negative"
    assert target_brand_case.evidence_quote in target_risk_text


def test_execute_llm_error_falls_back_to_dictionary_with_failure_note() -> None:
    judge = _FakeJudge(_GOOD, error=JudgeError("ReadTimeout"))
    result, sink = _execute(context=_context(), judge=judge)
    assert result.failures, "LLM 失败必须如实记 failures"
    assert all("llm_error" in f.error for f in result.failures)
    assert sink.records and all(r.method == METHOD_DICTIONARY for r in sink.records)
    assert result.dictionary_fallback == len(sink.records)


def test_execute_idempotent_skip_existing() -> None:
    # 先用真实切窗算出全部幂等键，灌进 existing_keys
    windows = extract_windows(
        subject_type="answer",
        subject_pub_id="ans_x",
        text=_ANSWER_TEXT,
        brand=_BRAND,
        competitors=(),
        platform="doubao",
    ) + extract_windows(
        subject_type="source_document",
        subject_pub_id="srd_x",
        text=_DOC_TEXT,
        brand=_BRAND,
        competitors=(),
        platform="a.example.com",
        source_url="https://a.example.com/article",
    )
    keys = frozenset(
        (w.subject_pub_id, w.window_hash, w.target_brand, _LLM.model, PROMPT_VERSION)
        for w in dedupe_windows(windows)
    )
    judge = _FakeJudge(_GOOD)
    result, sink = _execute(context=_context(existing_keys=keys), judge=judge)
    assert judge.calls == [] and sink.records == []
    assert result.skipped == len(keys)


def test_execute_window_limit_truncates() -> None:
    judge = _FakeJudge(LlmJudgment("", "", "neutral", False, "中意人寿", 0.5))
    result, sink = _execute(context=_context(), judge=judge, window_limit=1)
    assert result.windows > 1
    assert len(sink.records) == 1
    assert result.truncated == result.windows - 1


def test_execute_cas_read_failure_goes_to_failures() -> None:
    store = _FakeTextStore(error=RuntimeError("minio down"))
    result, sink = _execute(context=_context(), text_store=store)
    assert any("minio down" in f.error for f in result.failures)
    # 文档窗未切，只有 answer 窗被判定
    assert all(r.subject_type == "answer" for r in sink.records)


def test_derive_judgment_pub_id_deterministic() -> None:
    a = derive_judgment_pub_id(_TENANT, _RUN, "ans_x", "h" * 64, "友邦", "m", PROMPT_VERSION)
    b = derive_judgment_pub_id(_TENANT, _RUN, "ans_x", "h" * 64, "友邦", "m", PROMPT_VERSION)
    c = derive_judgment_pub_id(_TENANT, _RUN, "ans_x", "h" * 64, "平安", "m", PROMPT_VERSION)
    assert a == b and a.startswith("dpj_") and len(a) == 30 and a != c
