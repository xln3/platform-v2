"""己方内容拉踩检测单测（judge_own_content_disparagement + SOP 定稿触发钩子）。

覆盖：brand_profile 解析（aliases/competitors 词表纪律）、SOP 触发门
（publication_ready false→true 才插 workflow_start_command，重复/翻回不插）、
execute 主流程（disabled / version_not_found / empty_body / no_competitors 安静
跳过 / LLM 正常判定 / 词典兜底 / validation_failure 丢弃 / 幂等跳过 / 窗数截断）。
依赖全 fake，绝不打真 LLM/DB。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest
from geo_platform.sop.service import (
    SopService,
    _enqueue_own_content_judgment,
    _should_trigger_own_content_judgment,
)
from temporalio.exceptions import ApplicationError

from domain.scoring.disparagement import (
    DICTIONARY_VERSION,
    METHOD_DICTIONARY,
    METHOD_LLM,
    PROMPT_VERSION,
    window_text_hash,
)
from workflows.activities.disparagement import (
    DisparagementRecord,
    JudgeError,
    LlmJudgment,
)
from workflows.activities.own_content_disparagement import (
    OwnContentContext,
    OwnContentDisparagementInput,
    execute_own_content_disparagement,
    parse_brand_profile,
)
from workflows.activities.source_audit import AuditLlmConfig

_TENANT = "tnt_0123456789abcdef"
_VERSION = "sav_0123456789abcdef"

_ITEM = OwnContentDisparagementInput(tenant_pub_id=_TENANT, article_version_pub_id=_VERSION)
_LLM = AuditLlmConfig(api_key="k", model="gpt-5.6-luna", base_url="https://aihubmix.com")
_LLM_NO_KEY = AuditLlmConfig(api_key="", model="gpt-5.6-luna", base_url="https://aihubmix.com")

_BRAND = "盛邦安全"
_COMPETITORS = ("奇安信", "深信服")
_BODY = (
    "从实测数据看，奇安信的老一代边界产品规则库更新滞后，防护效果堪忧，"
    "而盛邦安全的 RayTAG 持续检测能力值得企业信赖。深信服的方案在旁路部署场景"
    "也有不错的口碑。"
)


def _context(
    *,
    body: str = _BODY,
    competitors: tuple[str, ...] = _COMPETITORS,
    existing_keys: frozenset[tuple[str, str, str, str]] = frozenset(),
) -> OwnContentContext:
    return OwnContentContext(
        tenant_pub_id=_TENANT,
        tenant_id="00000000-0000-0000-0000-000000000001",
        article_version_pub_id=_VERSION,
        sop_project_pub_id="spr_0123456789abcdef",
        title="稿件标题",
        body=body,
        brand=_BRAND,
        aliases=(),
        competitors=competitors,
        existing_keys=existing_keys,
    )


class _FakeLoader:
    def __init__(self, context: OwnContentContext | None) -> None:
        self._context = context

    def load(self, tenant_pub_id: str, article_version_pub_id: str) -> OwnContentContext | None:
        return self._context


class _FakeJudge:
    def __init__(self, outcome: LlmJudgment | None = None, error: Exception | None = None) -> None:
        self._outcome = outcome or LlmJudgment("", "", "negative", True, "", 0.9)
        self._error = error
        self.calls: list[str] = []

    def judge(
        self, *, window_text: str, target_brand: str, known_brands: tuple[str, ...]
    ) -> LlmJudgment:
        self.calls.append(target_brand)
        if self._error is not None:
            raise self._error
        return LlmJudgment(
            subject=self._outcome.subject,  # 缺省 ""=文本本身（品牌窗填品牌会自指校验失败）
            target=target_brand,
            attitude=self._outcome.attitude,
            disparagement=self._outcome.disparagement,
            evidence_quote=self._outcome.evidence_quote or target_brand,  # 逐字必中窗文本
            confidence=self._outcome.confidence,
        )


class _FakeSink:
    def __init__(self) -> None:
        self.records: list[DisparagementRecord] = []

    def persist(self, *, context: OwnContentContext, record: DisparagementRecord) -> str:
        self.records.append(record)
        return "dpj_fake"


def _execute(
    *,
    context: OwnContentContext | None,
    judge: _FakeJudge | None = None,
    llm: AuditLlmConfig = _LLM,
    sink: _FakeSink | None = None,
    enabled: bool = True,
    window_limit: int = 50,
):
    used_sink = sink or _FakeSink()
    result = execute_own_content_disparagement(
        _ITEM,
        enabled=enabled,
        window_limit=window_limit,
        llm=llm,
        judge=judge if judge is not None else _FakeJudge(),
        loader=_FakeLoader(context),
        sink=used_sink,
    )
    return result, used_sink


# ---------------------------------------------------------------------------
# brand_profile 解析
# ---------------------------------------------------------------------------


def test_parse_brand_profile_strict() -> None:
    aliases, competitors = parse_brand_profile(
        {"aliases": ["WebRAY", " 远江盛邦 "], "competitors": ["奇安信", 123, "", "奇安信"]}
    )
    assert aliases == ("WebRAY", "远江盛邦")
    assert competitors == ("奇安信",)  # 非串/空/重复一律剔除
    assert parse_brand_profile({}) == ((), ())
    assert parse_brand_profile({"competitors": "奇安信"}) == ((), ())  # 非列表不认


# ---------------------------------------------------------------------------
# SOP 定稿触发门
# ---------------------------------------------------------------------------


def test_trigger_gate_only_on_finalize_transition() -> None:
    prior_draft = {"publication_ready": False}
    prior_ready = {"publication_ready": True}
    assert _should_trigger_own_content_judgment(prior_draft, {"publication_ready": True})
    assert not _should_trigger_own_content_judgment(prior_ready, {"publication_ready": True})
    assert not _should_trigger_own_content_judgment(prior_draft, {"publication_ready": False})
    assert not _should_trigger_own_content_judgment(prior_draft, {"title": "只改标题"})
    assert not _should_trigger_own_content_judgment(prior_ready, {})


class _RecordingCursor:
    def __init__(self, connection: _RecordingConnection, row: Any = None) -> None:
        self._connection = connection
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _RecordingConnection:
    """最小 fake psycopg 连接：按 SQL 文本路由返回值，记录全部调用。"""

    def __init__(self, version_row: dict[str, Any]) -> None:
        self._version_row = version_row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _RecordingCursor:
        self.calls.append((sql, params))
        if "sop.article_version" in sql:  # SELECT ... FOR UPDATE 与 UPDATE ... RETURNING
            return _RecordingCursor(self, dict(self._version_row))
        return _RecordingCursor(self, None)


def _service_with_fake_conn(connection: _RecordingConnection) -> SopService:
    service = SopService(dsn="postgresql://fake")

    @contextmanager
    def fake_conn(tenant_pub_id: str):
        yield connection

    service._conn = fake_conn  # type: ignore[method-assign]
    return service


def test_update_article_version_enqueues_workflow_on_finalize() -> None:
    connection = _RecordingConnection(
        {"pub_id": _VERSION, "publication_ready": False, "readiness_checklist": None}
    )
    service = _service_with_fake_conn(connection)
    service.update_article_version(
        tenant_pub_id=_TENANT,
        version_pub_id=_VERSION,
        readiness_checklist=None,
        fields={"publication_ready": True},
    )
    inserts = [call for call in connection.calls if "workflow_start_command" in call[0]]
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "ON CONFLICT (workflow_id) DO NOTHING" in sql
    _command_id, tenant, workflow_type, workflow_id, _queue, payload, _trace = params
    assert tenant == _TENANT
    assert workflow_type == "own_content_disparagement"
    assert workflow_id == f"own-content-disparagement/{_TENANT}/{_VERSION}"
    assert json.loads(payload) == {
        "tenant_pub_id": _TENANT,
        "article_version_pub_id": _VERSION,
    }


def test_update_article_version_no_trigger_without_transition() -> None:
    connection = _RecordingConnection(
        {"pub_id": _VERSION, "publication_ready": True, "readiness_checklist": None}
    )
    service = _service_with_fake_conn(connection)
    service.update_article_version(
        tenant_pub_id=_TENANT,
        version_pub_id=_VERSION,
        readiness_checklist=None,
        fields={"publication_ready": True},  # 已 ready 重复 PATCH
    )
    assert not [call for call in connection.calls if "workflow_start_command" in call[0]]


def test_enqueue_is_idempotent_by_workflow_id() -> None:
    """同一版本重复入队：workflow_id 确定性相同 → ON CONFLICT 兜底（语句级锁定）。"""
    connection = _RecordingConnection({"pub_id": _VERSION})
    _enqueue_own_content_judgment(connection, tenant_pub_id=_TENANT, version_pub_id=_VERSION)
    _enqueue_own_content_judgment(connection, tenant_pub_id=_TENANT, version_pub_id=_VERSION)
    inserts = [call for call in connection.calls if "workflow_start_command" in call[0]]
    assert len(inserts) == 2  # 两次都发 INSERT
    assert inserts[0][1][3] == inserts[1][1][3]  # workflow_id 相同 → DB 去重


# ---------------------------------------------------------------------------
# execute 主流程
# ---------------------------------------------------------------------------


def test_execute_disabled_zero_io() -> None:
    result, sink = _execute(context=None, enabled=False)
    assert result.disabled is True
    assert sink.records == []


def test_execute_version_not_found_non_retryable() -> None:
    with pytest.raises(ApplicationError, match="article version not found"):
        _execute(context=None)


def test_execute_empty_body_quiet_skip() -> None:
    judge = _FakeJudge()
    result = execute_own_content_disparagement(
        _ITEM,
        enabled=True,
        window_limit=50,
        llm=_LLM,
        judge=judge,
        loader=_FakeLoader(_context(body="   \n ")),
        sink=_FakeSink(),
    )
    assert result.skipped == "empty_body"
    assert judge.calls == []


def test_execute_no_competitors_quiet_skip() -> None:
    judge = _FakeJudge()
    result = execute_own_content_disparagement(
        _ITEM,
        enabled=True,
        window_limit=50,
        llm=_LLM,
        judge=judge,
        loader=_FakeLoader(_context(competitors=())),
        sink=_FakeSink(),
    )
    assert result.skipped == "no_competitors"
    assert judge.calls == []


def test_execute_judges_windows_and_marks_own_content() -> None:
    result, sink = _execute(context=_context())
    assert result.windows >= 3  # 两竞品提及 + 品牌提及 + 竞品共现窗
    assert result.judged == result.windows
    for record in sink.records:
        assert record.subject_type == "own_content"
        assert record.subject_pub_id == _VERSION
        assert record.platform == "own_content"
        assert record.method == METHOD_LLM
        assert record.prompt_version == PROMPT_VERSION
        assert record.judgment_status == "ok"


def test_execute_llm_unavailable_dictionary_fallback() -> None:
    result, sink = _execute(context=_context(), llm=_LLM_NO_KEY)
    assert result.judged == result.windows > 0
    assert result.dictionary_fallback == result.judged
    for record in sink.records:
        assert record.method == METHOD_DICTIONARY
        assert record.prompt_version == DICTIONARY_VERSION
        assert record.model == ""


def test_execute_judge_error_falls_back_to_dictionary() -> None:
    result, sink = _execute(context=_context(), judge=_FakeJudge(error=JudgeError("boom")))
    assert result.judged == result.windows > 0
    assert result.dictionary_fallback == result.judged
    assert len(result.failures) == result.windows  # 每窗 llm_error 如实留痕
    assert all(record.method == METHOD_DICTIONARY for record in sink.records)


def test_execute_validation_failure_discards_score() -> None:
    bad = LlmJudgment("", "", "negative", True, "窗里根本没有这句话", 0.9)
    result, sink = _execute(context=_context(), judge=_FakeJudge(outcome=bad))
    assert result.validation_failures == result.windows > 0
    assert result.judged == 0
    for record in sink.records:
        assert record.judgment_status == "validation_failure"
        assert record.attitude is None and record.disparagement is None


def test_execute_idempotent_skip_existing_keys() -> None:
    # 预置：全部窗的幂等键（词典口径 model="" 不影响——这里用 LLM 口径键）
    from domain.scoring.disparagement import dedupe_windows, extract_windows

    windows = dedupe_windows(
        extract_windows(
            subject_type="own_content",
            subject_pub_id=_VERSION,
            text=_BODY,
            brand=_BRAND,
            competitors=_COMPETITORS,
            platform="own_content",
        )
    )
    existing = frozenset(
        (w.window_hash, w.target_brand, "gpt-5.6-luna", PROMPT_VERSION) for w in windows
    )
    judge = _FakeJudge()
    result, sink = _execute(context=_context(existing_keys=existing), judge=judge)
    assert result.skipped_idempotent == result.windows > 0
    assert judge.calls == [] and sink.records == []


def test_execute_legacy_window_hint_does_not_truncate() -> None:
    result, _ = _execute(context=_context(), window_limit=1)
    assert result.windows > 1
    assert result.judged == result.windows
    assert result.truncated == 0


def test_window_hash_stable_for_verbatim_check() -> None:
    """own_content 切窗与采集侧同函数同窗 hash（同文重切不重复判定）。"""
    assert window_text_hash("盛邦安全 值得推荐") == window_text_hash("盛邦安全\n值得推荐")
