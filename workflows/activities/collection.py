import json
import math
import mimetypes
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import structlog
from geo_platform.collection.account_governor import AccountGovernor
from geo_platform.collection.leases import acquire_session_lease
from geo_platform.collection.models import (
    AccountAuthorization,
    BrowserProfile,
    CapabilityLease,
    CollectionRun,
    CollectionTask,
    DeviceBinding,
    InterventionRequest,
    PlatformAccount,
    RevocationRequest,
    SessionEvent,
    SessionLease,
    TerminalTask,
)
from geo_platform.collection.retry_queue import (
    mark_source_retry_outcome,
    record_query_attempt,
    record_query_failure_knowledge,
    record_run_failure_knowledge,
)
from geo_platform.collection.vault import KmsUnavailableError, VaultTransitKms
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.projects.models import Brand, Competitor, MonitoringConfigVersion, Project
from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.repository import TenantRepository
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.collection.answer_content import project_answer_content
from domain.collection.uvw import (
    URL_NORMALIZATION_VERSION,
    legacy_reference_event,
    normalize_retrieval_events,
    occurrence_rows,
)
from domain.evidence.dlp import assert_secret_free
from domain.source_analysis.page_inspection import (
    PAGE_INSPECTION_POLICY_VERSION,
    PAGE_INSPECTION_PROMPT_VERSION,
    derive_page_inspection_version,
)
from workflows.activities.analysis_jobs import (
    ANSWER_BASIC_POLICY_VERSION,
    POST_COLLECTION_POLICY_VERSION,
    RUN_ANALYZER_KINDS,
    canonical_input_hash,
    enqueue_analysis_job,
)
from workflows.activities.browser_router import account_governance_enabled
from workflows.activities.official_share import (
    TONGYI_OFFICIAL_SHARE_HOSTS,
    YIYAN_OFFICIAL_SHARE_HOSTS,
)

log = structlog.get_logger()


@dataclass
class CollectionTaskInput:
    business_key: str
    query: str
    model: str
    region: str
    mode: str
    adapter: str = "fixed"
    fail_until_attempt: int = 0


# 平台 × mode 能力表（20260810 起，run 矩阵过滤真源）：只列各适配器实际支持的
# mode——deepseek 专家模式不支持搜索，GEO 评测不测专家（normal=快速+搜索开+
# 思考关，deep_think=快速+搜索开+思考开，见 deepseek_adapter docstring）；元宝
# 联网检索为平台自动行为（无开关），normal=Hy3+思考关，deep_think=Hy3+思考开
# （=hunyuan_t1，见 yuanbao_adapter docstring）；yiyan deep_think=composer
# 「深度思考」chip 开（20260810 live 校准，见 yiyan_adapter docstring）；通义
# deep_think=composer radix 菜单选「思考研究」（20260810 live 探针实证键盘路径，
# 见 tongyi_adapter docstring）。
# 未知平台 slug 不在表内 → 不过滤（dispatcher 会诚实报 unsupported adapter）。
# provider_api 模态（2026-08-31 起，provider_api_adapter.py）：官方 API 只采
# normal——API 侧没有「网页交互模式」语义；deep_think 待各家 reasoning 参数
# 逐平台校准后再开。
PLATFORM_MODE_CAPABILITIES: dict[str, frozenset[str]] = {
    "doubao": frozenset({"normal", "deep_think"}),
    "deepseek": frozenset({"normal", "deep_think"}),
    "tongyi": frozenset({"normal", "deep_think"}),
    "yiyan": frozenset({"normal", "deep_think"}),
    "yuanbao": frozenset({"normal", "deep_think"}),
    "doubao_api": frozenset({"normal"}),
    "deepseek_api": frozenset({"normal"}),
    "tongyi_api": frozenset({"normal"}),
    "yiyan_api": frozenset({"normal"}),
    "yuanbao_api": frozenset({"normal"}),
}

# provider_api 采集模态（ADR-0008 三采集面之一）的 v1 管线 adapter slug 集
# （2026-08-31 起）：官方 API 直连、无浏览器/无地域出口。run_service
# ._task_matrix 据此把 region 折叠为哨兵 API_SURFACE_REGION（不做地域伪装）；
# INV-1 geo provenance 对这些 slug 无出口声明 → geo_source=unverified →
# measurement_eligible=False，测量读面（answer_agg_blind/brandrank eligible
# 过滤）自动排除，consumer_web 分母零污染。执行体=provider_api_adapter.py。
PROVIDER_API_ADAPTER_SLUGS = frozenset(
    {"doubao_api", "deepseek_api", "yiyan_api", "tongyi_api", "yuanbao_api"}
)
API_SURFACE_REGION = "api"


@dataclass
class CollectionEvidenceRef:
    kind: str
    path: str
    relation_type: str
    mime_type: str
    source_url: str | None = None
    title: str | None = None
    cited_text: str | None = None
    ordinal: int | None = None
    anchors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CollectionTaskResult:
    business_key: str
    answer_text: str
    screenshot_ref: str
    quality_state: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    # 平台真实检索词（W1）：[{"query": ..., "ordinal": ...}]；无检索词为空列表。
    search_queries: list[dict[str, Any]] = field(default_factory=list)
    # 一次回答内逐次检索的 U/V/最终引用现场事实。URL 身份可在落库时聚合，
    # 这里的事件和候选 occurrence 永不去重或按总量截断。
    retrieval_events: list[dict[str, Any]] = field(default_factory=list)


# collect_doubao_batch 的 per-item 结果状态词表：ok=采集成功；wall=平台墙/阻断性
# 失败（non_retryable 语义）；incomplete=采集未完成的诚实失败（可重试语义）；
# aborted=batch 内前序题失败后本题未执行（真人撞墙后会停下——零浏览器交互、
# 不编造不硬闯）。
COLLECTION_BATCH_ITEM_STATUSES = frozenset({"ok", "wall", "incomplete", "aborted"})


@dataclass
class CollectionBatchItemResult:
    """batch 采集（collect_doubao_batch）的 per-item 结果，Temporal 可序列化。

    与 CollectionTaskResult 字段对齐（ok 题携带其全部字段），另加
    status/error_type/error_message 诚实失败信息。除 business_key 外全部带
    默认值：旧 per-task 路径 persist_collection_result 的历史 payload
    （CollectionTaskResult 形状、无 status 字段）反序列化后 status="ok"，
    行为与旧形状完全一致（replay 安全）。

    ``browser_instance``（2026-08-09 起，浏览器矩阵化）：本 batch 实际使用的
    常驻实例键（``doubao_sh`` 等，由 browser_router 解析）；persist 层把它记入
    collection_task.matrix_json，fanout 的 INV-1 geo provenance 优先按它查
    出口省码。旧 payload 无此字段 → None → matrix 不写该键（零漂移）。
    """

    business_key: str
    status: str = "ok"
    error_type: str | None = None
    error_message: str | None = None
    answer_text: str | None = None
    screenshot_ref: str | None = None
    quality_state: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[CollectionEvidenceRef] = field(default_factory=list)
    search_queries: list[dict[str, Any]] = field(default_factory=list)
    retrieval_events: list[dict[str, Any]] = field(default_factory=list)
    browser_instance: str | None = None


@dataclass
class CollectionBatchInput:
    """collect_doubao_batch 输入：同一 run 内按原相对顺序排列的同平台任务。"""

    tenant_pub_id: str
    run_pub_id: str
    items: list[CollectionTaskInput]


@dataclass
class CaptchaPause:
    """batch 内撞验证码的挂起请求。``resume_index`` = 撞码题在输入 items 里的
    下标（该题结果即 ``results[resume_index]``，error_type=="wall_captcha"）。
    evidence_ref 为存证截图 ref（file:// 形式，可空）。session_id 无关——
    关联 id 由 assist activity 铸造后返回给 workflow。

    ``instance_key``（2026-08-09 起，浏览器矩阵化）：撞码 batch 实际使用的常驻
    实例键——assist 接管必须 attach **同一台**常驻浏览器（锁/CDP/fence 都按
    实例键）。旧历史 payload 无此字段 → None → assist 回退按平台 slug 取锁/CDP
    （启用矩阵化前的行为，replay 安全）。"""

    resume_index: int
    business_key: str
    wall_type: str = "wall_captcha"
    evidence_ref: str | None = None
    instance_key: str | None = None


@dataclass
class CollectionBatchResult:
    """batch 输出：结果列表与输入 items 等长同序（失败/未执行题也占位）。

    墙类失败不 raise——诚实记录在 per-item 结果里；仅配置类错误
    （adapter_not_configured/unsupported_mode）允许 raise。

    ``captcha_pause``（captcha-assist-v1）：撞验证码时由 live 适配器标记，
    ``results`` 仍保持等长（wall + aborted 全占位——未打补丁的旧 workflow
    重放本结果行为与今天完全一致）；新 workflow 只落 ``resume_index`` 前
    缀，挂起等人工接管后从 ``resume_index`` 起重采（撞码题本身重发）。
    """

    results: list[CollectionBatchItemResult] = field(default_factory=list)
    captcha_pause: CaptchaPause | None = None


def batch_result_with_captcha_pause(
    results: list[CollectionBatchItemResult],
    *,
    instance_key: str | None = None,
) -> CollectionBatchResult:
    """等长结果 → CollectionBatchResult；首个 wall_captcha 题标注 captcha_pause。

    captcha-assist-v1：撞码是可人工恢复的暂停点而非终局失败——workflow 见到
    pause 挂起等人工接管、从 resume_index 起重采；results 仍等长全占位（旧
    workflow 重放行为不变）。非撞码失败不产生 pause。五平台 batch 统一出口。

    ``instance_key``（浏览器矩阵化）：batch 出口统一盖实例章——逐结果写
    ``browser_instance``（persist 进 matrix_json 的 provenance 来源）且 pause
    携带实例键（assist 接管 attach 同一台常驻浏览器）。None = 旧行为不变。
    """
    if instance_key is not None:
        for result in results:
            result.browser_instance = instance_key
    for index, result in enumerate(results):
        if result.status == "wall" and result.error_type == "wall_captcha":
            return CollectionBatchResult(
                results=results,
                captcha_pause=CaptchaPause(
                    resume_index=index,
                    business_key=result.business_key,
                    wall_type=result.error_type,
                    evidence_ref=result.screenshot_ref,
                    instance_key=instance_key,
                ),
            )
    return CollectionBatchResult(results=results)


@activity.defn(name="collect_doubao_batch")
async def collect_doubao_batch(batch: CollectionBatchInput) -> CollectionBatchResult:
    """Fail-closed batch adapter boundary（与 collect_with_adapter 同款默认实现）。

    worker 部署必须用 live 豆包适配器实现替换本注册（workers/main.py 按
    GEO_COLLECTION_ADAPTER 门控选择）。
    """
    activity.heartbeat({"run_pub_id": batch.run_pub_id, "stage": "adapter_started"})
    raise ApplicationError(
        "no live collection adapter is registered",
        type="adapter_not_configured",
        non_retryable=True,
    )


def _make_fail_closed_batch(slug: str) -> Callable[..., Any]:
    """生成与 collect_doubao_batch 同款的 fail-closed batch 默认实现（W8 五平台）。

    workflow 按 slug 查 callable 派发（字符串名派发会把结果转成 dict 导致
    workflow 任务无限重试——2026-08-06 实测坑），因此默认实现也必须是具名
    callable；workers/main.py 按 GEO_COLLECTION_ADAPTER 门控替换为 live 实现。
    """

    @activity.defn(name=f"collect_{slug}_batch")
    async def _stub(batch: CollectionBatchInput) -> CollectionBatchResult:
        activity.heartbeat({"run_pub_id": batch.run_pub_id, "stage": "adapter_started"})
        raise ApplicationError(
            f"no live {slug} batch adapter is registered",
            type="adapter_not_configured",
            non_retryable=True,
        )

    return _stub


collect_deepseek_batch = _make_fail_closed_batch("deepseek")
collect_tongyi_batch = _make_fail_closed_batch("tongyi")
collect_yiyan_batch = _make_fail_closed_batch("yiyan")
collect_yuanbao_batch = _make_fail_closed_batch("yuanbao")

# provider_api 模态五个 fail-closed 默认（2026-08-31 起）；live 实现=
# provider_api_adapter.py，workers/main.py 在 GEO_COLLECTION_ADAPTER=multi 下替换。
collect_doubao_api_batch = _make_fail_closed_batch("doubao_api")
collect_deepseek_api_batch = _make_fail_closed_batch("deepseek_api")
collect_yiyan_api_batch = _make_fail_closed_batch("yiyan_api")
collect_tongyi_api_batch = _make_fail_closed_batch("tongyi_api")
collect_yuanbao_api_batch = _make_fail_closed_batch("yuanbao_api")


@dataclass
class SessionPreparation:
    lease_pub_id: str
    fencing_token: int
    profile_version: int


@dataclass
class RevocationResult:
    account_pub_id: str
    released_leases: int
    purged_profile_versions: list[int]
    revoked_device_bindings: int
    revoked_terminal_tasks: int
    revoked_interventions: int
    revoked_capability_leases: int
    deletion_verified: bool


_EVIDENCE_KINDS = {
    "answer_screenshot",
    "answer_excerpt_screenshot",
    "share_image",
    "share_link",
    "share_verification",
    "source_screenshot",
    "sse",
    # 原始流量留痕（2026-08-10 起，用户拍板默认开）：sse_raw=completion 端点
    # 原始响应体；har=本题页面级 HAR 1.2。绝不可复用 kind="sse"——trace 端点
    # 硬过滤 kind='sse' AND relation='answer_sse_trace'，复用会污染读面。
    "sse_raw",
    "har",
    # provider_api 模态（2026-08-31 起）：官方 API 原始响应 JSON 原文，
    # sse_raw 的 API 对照物（provider_api_adapter.py）。
    "provider_api_raw",
}
_EVIDENCE_RELATIONS = {
    "answer_page",
    "answer_evidence_excerpt",
    "official_share_image",
    "official_share_link",
    "official_share_verification",
    "cited_source_snapshot",
    "ai_opened_source_preview",
    "answer_sse_trace",
    "answer_sse_raw",
    "answer_har",
    "answer_provider_api_raw",
}
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_MAX_EVIDENCE_BYTES = 30 * 1024 * 1024


def _normalize_search_queries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """平台真实检索词（W1）规范化：[{"query": str, "ordinal": int}]。

    原始采集原则：平台输出是测量原料，**原文存储、不做任何脱敏**
    （2026-08-06 用户拍板；DLP 只管会话侧秘密/intake 边界，不碰公开内容）。
    """
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("collection search query must be an object")
        query = item.get("query")
        ordinal = item.get("ordinal")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("collection search query text is invalid")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise ValueError("collection search query ordinal is invalid")
        normalized.append({"query": query.strip(), "ordinal": ordinal})
    return normalized


def _safe_http_url(value: str | None) -> str | None:
    """结构校验（scheme/host/无内嵌凭据/长度）。URL 是公开平台输出（引用/信源
    页面地址），按 2026-08-06 拍板原文存储零 DLP——公开 URL 里的长数字串
    （文章 id 等）会误命中 phone 模式，绝不允许因此拒绝测量原料。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise ValueError("evidence source URL is invalid")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("evidence source URL must use HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("evidence source URL must not contain credentials")
    return value


def _normalize_citations(
    items: list[dict[str, Any]], *, answer_text: str = ""
) -> list[dict[str, Any]]:
    """Validate citations while preserving their real platform ordinals.

    Repeated URLs are not discarded here: two platform ordinals may point to
    the same source and are still two answer→source relations.  W2 performs its
    own URL-level fetch deduplication without destroying those relations.
    """
    markers = [int(value) for value in re.findall(r"\[citation:(\d+)\]", answer_text, re.I)]
    declared_ordinals = [
        item.get("platform_ordinal", item.get("ordinal"))
        for item in items
        if isinstance(item, dict)
    ]
    inferred_base = 0 if 0 in markers or 0 in declared_ordinals else 1
    normalized: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    seen_bases: set[int] = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("collection citation must be an object")
        url = _safe_http_url(item.get("url"))
        assert url is not None
        title = item.get("title")
        cited_text = item.get("cited_text")
        if title is not None:
            if not isinstance(title, str) or not title.strip() or len(title) > 300:
                raise ValueError("collection citation title is invalid")
            title = title.strip()
        if cited_text is not None:
            if not isinstance(cited_text, str) or not cited_text.strip():
                cited_text = None
            elif len(cited_text) > 2_000:
                cited_text = cited_text[:2_000]
            if cited_text:
                cited_text = cited_text.strip()
        raw_base = item.get("ordinal_base", inferred_base)
        if not isinstance(raw_base, int) or isinstance(raw_base, bool) or raw_base not in {0, 1}:
            raise ValueError("collection citation ordinal base is invalid")
        seen_bases.add(raw_base)
        if len(seen_bases) > 1:
            raise ValueError("collection citation ordinal bases are inconsistent")
        raw_platform_ordinal = item.get("platform_ordinal", item.get("ordinal"))
        if raw_platform_ordinal is None:
            platform_ordinal = index - 1 if raw_base == 0 else index
        elif (
            not isinstance(raw_platform_ordinal, int)
            or isinstance(raw_platform_ordinal, bool)
            or raw_platform_ordinal < raw_base
        ):
            raise ValueError("collection citation platform ordinal is invalid")
        else:
            platform_ordinal = raw_platform_ordinal
        ordinal = platform_ordinal + 1 if raw_base == 0 else platform_ordinal
        if ordinal < 1 or ordinal in seen_ordinals:
            raise ValueError("collection citation ordinal is duplicate or invalid")
        seen_ordinals.add(ordinal)
        normalized.append(
            {
                "url": url,
                "title": title,
                "cited_text": cited_text,
                "ordinal": ordinal,
                "platform_ordinal": platform_ordinal,
                "ordinal_base": raw_base,
            }
        )
    return normalized


_HEX_TOKEN_RE = re.compile(r"[0-9a-f]{32,64}")


def _assert_system_ref_secret_free(value: str) -> None:
    """系统自产引用（证据路径主干=business key/sha256 hex、error_type 词表）的秘密自检。

    64-hex 主干里的随机数字串可能撞上手机号正则（20260813 生产实证：yiyan 证据
    文件名 …a17581478051f… 命中 1[3-9]\\d{9} → collection_result_dlp_rejected，
    整个 544 题 run 被误杀）。hex token 按 opaque 剥离后再扫描；路径目录、
    error_type 词表等其余字符照常 fail-closed。
    """
    assert_secret_free(_HEX_TOKEN_RE.sub("", value))


def _path_from_evidence_ref(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise ValueError("collection evidence path is invalid")
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("collection evidence must be a local file")
        value = unquote(parsed.path)
    path = Path(value).resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError("collection evidence is not a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_EVIDENCE_BYTES:
        raise ValueError("collection evidence size is outside the allowed range")
    _assert_system_ref_secret_free(str(path))
    return path


def _normalize_evidence_refs(
    result: CollectionTaskResult | CollectionBatchItemResult,
) -> list[CollectionEvidenceRef]:
    raw_items = list(getattr(result, "evidence", []) or [])
    has_answer_screenshot = any(
        (item.get("kind") if isinstance(item, dict) else item.kind) == "answer_screenshot"
        for item in raw_items
    )
    if (
        result.screenshot_ref
        and result.screenshot_ref.startswith("file://")
        and not has_answer_screenshot
    ):
        raw_items.insert(
            0,
            CollectionEvidenceRef(
                kind="answer_screenshot",
                path=result.screenshot_ref,
                relation_type="answer_page",
                mime_type="image/png",
                source_url=None,
            ),
        )
    answer_text = result.answer_text if isinstance(result.answer_text, str) else None
    return _normalize_evidence_list(raw_items, answer_text=answer_text)


def _evidence_mime_from_name(name: str) -> str | None:
    """mime 推断补充（2026-08-10）：自有证据后缀显式映射——mimetypes 对
    ``-har.json`` 只会猜出 application/json、对 ``-sse-raw.txt`` 猜 text/plain，
    都不是进 CAS 的权威 mime（har=application/har+json 是 DLP JSON-aware 词表
    成员，sse_raw=text/event-stream）。"""
    if name.endswith("-har.json"):
        return "application/har+json"
    if name.endswith("-sse-raw.txt"):
        return "text/event-stream"
    return mimetypes.guess_type(name)[0]


def _normalize_evidence_list(
    raw_items: list[Any], *, answer_text: str | None = None
) -> list[CollectionEvidenceRef]:
    """证据列表逐条规范化（kind/relation 词表 + 本地路径 + mime + URL 形状）。

    ok 题经 ``_normalize_evidence_refs``（含截图前置）调用；失败题直接对
    ``result.evidence`` 调用——墙截图维持现状不进 CAS（不过截图前置），只有
    adapter 显式携带的 ref（raw/HAR）进证据链。"""
    if len(raw_items) > 50:
        raise ValueError("collection result has too many evidence assets")
    normalized: list[CollectionEvidenceRef] = []
    for raw in raw_items:
        item = CollectionEvidenceRef(**raw) if isinstance(raw, dict) else raw
        if not isinstance(item, CollectionEvidenceRef):
            raise ValueError("collection evidence reference is invalid")
        if item.kind not in _EVIDENCE_KINDS or not _SAFE_TOKEN_RE.fullmatch(item.kind):
            raise ValueError("collection evidence kind is invalid")
        if item.relation_type not in _EVIDENCE_RELATIONS or not _SAFE_TOKEN_RE.fullmatch(
            item.relation_type
        ):
            raise ValueError("collection evidence relation is invalid")
        path = _path_from_evidence_ref(item.path)
        mime_type = item.mime_type or _evidence_mime_from_name(path.name)
        if not mime_type or len(mime_type) > 120:
            raise ValueError("collection evidence MIME type is invalid")
        source_url = _safe_http_url(item.source_url)
        title = (
            item.title.strip()[:300] if isinstance(item.title, str) and item.title.strip() else None
        )
        cited_text = (
            item.cited_text.strip()[:2_000]
            if isinstance(item.cited_text, str) and item.cited_text.strip()
            else None
        )
        # title/cited_text 是信源页标题与提及段落原文（公开平台输出）——按
        # 2026-08-06 拍板原文存储零 DLP（营销稿含 400 电话等字样属正常，
        # 绝不因此拒绝测量原料）；DLP 只守本地自产路径串。
        if item.ordinal is not None and (not isinstance(item.ordinal, int) or item.ordinal < 1):
            raise ValueError("collection evidence ordinal is invalid")
        anchors = _normalize_evidence_anchors(item.anchors, answer_text=answer_text)
        is_answer_excerpt = item.kind == "answer_excerpt_screenshot"
        if is_answer_excerpt != (item.relation_type == "answer_evidence_excerpt"):
            raise ValueError("answer evidence kind and relation must be paired")
        if is_answer_excerpt and not anchors:
            raise ValueError("answer evidence screenshot requires a verified anchor")
        if anchors and not is_answer_excerpt:
            raise ValueError("collection evidence anchors require an answer evidence screenshot")
        if is_answer_excerpt:
            _verify_answer_evidence_dimensions(path, anchors)
        normalized.append(
            CollectionEvidenceRef(
                kind=item.kind,
                path=str(path),
                relation_type=item.relation_type,
                mime_type=mime_type,
                source_url=source_url,
                title=title,
                cited_text=cited_text,
                ordinal=item.ordinal,
                anchors=anchors,
            )
        )
    return normalized


def _verify_answer_evidence_dimensions(path: Path, anchors: list[dict[str, Any]]) -> None:
    """Bind every persisted rectangle to the decoded CAS candidate dimensions."""

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_width, image_height = image.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("answer evidence screenshot is not a valid image") from error
    if (
        image_width <= 0
        or image_height <= 0
        or image_width > 100_000
        or image_height > 100_000
        or image_width * image_height > 100_000_000
    ):
        raise ValueError("answer evidence screenshot dimensions are invalid")
    for anchor in anchors:
        bbox = anchor["bbox"]
        if bbox["image_width"] != image_width or bbox["image_height"] != image_height:
            raise ValueError("answer evidence anchor dimensions do not match the image")


def _evidence_image_dimensions(path: Path, mime_type: str) -> tuple[int | None, int | None]:
    if not mime_type.startswith("image/"):
        return None, None
    expected_formats = {
        "image/png": {"PNG"},
        "image/jpeg": {"JPEG"},
        "image/jpg": {"JPEG"},
        "image/webp": {"WEBP"},
        "image/gif": {"GIF"},
        "image/bmp": {"BMP"},
        "image/tiff": {"TIFF"},
    }.get(mime_type.lower())
    if expected_formats is None:
        raise ValueError("collection image evidence MIME type is unsupported")
    try:
        with Image.open(path) as image:
            width, height = image.size
            decoded_format = image.format
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("collection image evidence is invalid") from error
    if decoded_format not in expected_formats:
        raise ValueError("collection image evidence MIME type does not match decoded bytes")
    if width <= 0 or height <= 0 or width > 100_000 or height > 100_000:
        raise ValueError("collection image evidence dimensions are invalid")
    if width * height > 150_000_000:
        raise ValueError("collection image evidence pixel count is too large")
    return width, height


def _normalize_evidence_anchors(
    raw_items: list[Any] | None, *, answer_text: str | None = None
) -> list[dict[str, Any]]:
    """Validate adapter-produced DOM/OCR text rectangles before persistence."""

    if raw_items is None:
        return []
    if not isinstance(raw_items, list) or len(raw_items) > 500:
        raise ValueError("collection evidence anchors are invalid")
    normalized: list[dict[str, Any]] = []
    previous_end = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("collection evidence anchor must be an object")
        start = raw.get("text_start")
        end = raw.get("text_end")
        anchor_text = raw.get("text")
        bbox = raw.get("bbox")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or not isinstance(anchor_text, str)
            or not anchor_text
            or len(anchor_text) != end - start
            or not isinstance(bbox, dict)
        ):
            raise ValueError("collection evidence anchor text interval is invalid")
        if start < previous_end:
            raise ValueError("collection evidence anchor intervals must be ordered")
        if answer_text is not None and (
            end > len(answer_text) or answer_text[start:end] != anchor_text
        ):
            raise ValueError("collection evidence anchor does not match the answer text")
        cleaned_bbox: dict[str, Any] = {}
        for key in ("x", "y", "width", "height", "image_width", "image_height"):
            value = bbox.get(key)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("collection evidence anchor bbox is invalid")
            number = float(value)
            if (
                not math.isfinite(number)
                or number < 0
                or (key in {"width", "height", "image_width", "image_height"} and number <= 0)
            ):
                raise ValueError("collection evidence anchor bbox is invalid")
            cleaned_bbox[key] = int(number) if number.is_integer() else number
        confidence = bbox.get("confidence", 1.0)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError("collection evidence anchor confidence is invalid")
        method = str(bbox.get("anchor_method") or "").strip()
        if not method or not _SAFE_TOKEN_RE.fullmatch(method):
            raise ValueError("collection evidence anchor method is invalid")
        if (
            cleaned_bbox["x"] + cleaned_bbox["width"] > cleaned_bbox["image_width"]
            or cleaned_bbox["y"] + cleaned_bbox["height"] > cleaned_bbox["image_height"]
        ):
            raise ValueError("collection evidence anchor bbox exceeds image dimensions")
        cleaned_bbox["confidence"] = float(confidence)
        cleaned_bbox["anchor_method"] = method
        ocr_version = bbox.get("ocr_version")
        if method.startswith("ocr_"):
            if (
                not isinstance(ocr_version, str)
                or not ocr_version.strip()
                or len(ocr_version.strip()) > 160
            ):
                raise ValueError("OCR evidence anchor version is invalid")
            cleaned_bbox["ocr_version"] = ocr_version.strip()
        elif ocr_version is not None:
            if not isinstance(ocr_version, str) or not ocr_version.strip():
                raise ValueError("collection evidence anchor OCR version is invalid")
            cleaned_bbox["ocr_version"] = ocr_version.strip()[:160]
        normalized.append(
            {
                "text_start": start,
                "text_end": end,
                "text": anchor_text,
                "bbox": cleaned_bbox,
            }
        )
        previous_end = end
    return normalized


def _persist_evidence_assets(
    *,
    session: Any,
    tenant_pub_id: str,
    project_pub_id: str,
    run_pub_id: str,
    answer_pub_id: str,
    business_key: str,
    adapter_version: str,
    evidence: list[CollectionEvidenceRef],
) -> dict[str, str]:
    if not evidence:
        return {}
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    store.ensure_bucket()
    capture_time = datetime.now(UTC)
    evidence_ids_by_relation: dict[str, str] = {}
    for index, item in enumerate(evidence, 1):
        customer_visible = (
            item.kind == "share_image" and item.relation_type == "official_share_image"
        )
        stable_key = "|".join(
            (
                tenant_pub_id,
                run_pub_id,
                business_key,
                item.kind,
                item.relation_type,
                str(item.ordinal or index),
            )
        )
        evidence_pub_id = f"evd_{sha256(stable_key.encode()).hexdigest()[:26]}"
        evidence_path = Path(item.path)
        image_width, image_height = _evidence_image_dimensions(evidence_path, item.mime_type)
        stored = store.put_redacted(evidence_path.read_bytes(), mime_type=item.mime_type)
        session.execute(
            text(
                """
                INSERT INTO evidence.evidence_asset
                  (pub_id,tenant_pub_id,project_pub_id,kind,access_class,sha256,object_key,
                   mime_type,byte_size,source_url,dlp_findings,channel,authorization_scope,
                   adapter_version,capture_time,authorized_session_capture,image_width,
                   image_height,customer_visible)
                VALUES
                  (:pub_id,:tenant_pub_id,:project_pub_id,:kind,'customer_private',:sha256,
                   :object_key,:mime_type,:byte_size,:source_url,:dlp_findings,'web',
                   CAST(:authorization_scope AS text[]),:adapter_version,:capture_time,false,
                   :image_width,:image_height,:customer_visible)
                ON CONFLICT (pub_id) DO NOTHING
                """
            ),
            {
                "pub_id": evidence_pub_id,
                "tenant_pub_id": tenant_pub_id,
                "project_pub_id": project_pub_id,
                "kind": item.kind,
                "sha256": stored.sha256,
                "object_key": stored.key,
                "mime_type": stored.mime_type,
                "byte_size": stored.byte_size,
                "source_url": item.source_url,
                "dlp_findings": list(stored.dlp_findings),
                "authorization_scope": [],
                "adapter_version": adapter_version,
                "capture_time": capture_time,
                "image_width": image_width,
                "image_height": image_height,
                "customer_visible": customer_visible,
            },
        )
        persisted = (
            session.execute(
                text(
                    """
                SELECT tenant_pub_id,project_pub_id,kind,sha256,object_key,mime_type,byte_size,
                       source_url,adapter_version,image_width,image_height,customer_visible
                FROM evidence.evidence_asset WHERE pub_id=:pub_id
                """
                ),
                {"pub_id": evidence_pub_id},
            )
            .mappings()
            .one()
        )
        expected = {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "kind": item.kind,
            "sha256": stored.sha256,
            "object_key": stored.key,
            "mime_type": stored.mime_type,
            "byte_size": stored.byte_size,
            "source_url": item.source_url,
            "adapter_version": adapter_version,
            "image_width": image_width,
            "image_height": image_height,
            "customer_visible": customer_visible,
        }
        if dict(persisted) != expected:
            raise ApplicationError(
                "evidence replay payload drifted",
                type="collection_evidence_payload_drift",
                non_retryable=True,
            )
        for anchor_index, anchor in enumerate(item.anchors, 1):
            anchor_text = str(anchor["text"])
            quote_hash = sha256(anchor_text.encode()).hexdigest()
            anchor_key = "|".join(
                (
                    evidence_pub_id,
                    str(anchor_index),
                    str(anchor["text_start"]),
                    str(anchor["text_end"]),
                    quote_hash,
                )
            )
            anchor_pub_id = f"anch_{sha256(anchor_key.encode()).hexdigest()[:25]}"
            bbox_json = json.dumps(
                anchor["bbox"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            session.execute(
                text(
                    """
                    INSERT INTO evidence.evidence_anchor
                      (pub_id,tenant_pub_id,evidence_pub_id,text_start,text_end,bbox,quote_hash)
                    VALUES
                      (:pub_id,:tenant_pub_id,:evidence_pub_id,:text_start,:text_end,
                       CAST(:bbox AS jsonb),:quote_hash)
                    ON CONFLICT (pub_id) DO NOTHING
                    """
                ),
                {
                    "pub_id": anchor_pub_id,
                    "tenant_pub_id": tenant_pub_id,
                    "evidence_pub_id": evidence_pub_id,
                    "text_start": anchor["text_start"],
                    "text_end": anchor["text_end"],
                    "bbox": bbox_json,
                    "quote_hash": quote_hash,
                },
            )
            persisted_anchor = (
                session.execute(
                    text(
                        """
                        SELECT tenant_pub_id,evidence_pub_id,text_start,text_end,bbox,quote_hash
                        FROM evidence.evidence_anchor WHERE pub_id=:pub_id
                        """
                    ),
                    {"pub_id": anchor_pub_id},
                )
                .mappings()
                .one()
            )
            expected_anchor = {
                "tenant_pub_id": tenant_pub_id,
                "evidence_pub_id": evidence_pub_id,
                "text_start": anchor["text_start"],
                "text_end": anchor["text_end"],
                "bbox": anchor["bbox"],
                "quote_hash": quote_hash,
            }
            if dict(persisted_anchor) != expected_anchor:
                raise ApplicationError(
                    "evidence anchor replay payload drifted",
                    type="collection_evidence_anchor_payload_drift",
                    non_retryable=True,
                )
        session.execute(
            text(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                VALUES (:tenant_pub_id,:from_pub_id,:to_pub_id,:relation_type)
                ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation_type) DO NOTHING
                """
            ),
            {
                "tenant_pub_id": tenant_pub_id,
                "from_pub_id": answer_pub_id,
                "to_pub_id": evidence_pub_id,
                "relation_type": item.relation_type,
            },
        )
        evidence_ids_by_relation[item.relation_type] = evidence_pub_id

    return evidence_ids_by_relation


_OFFICIAL_SHARE_HOSTS: dict[str, frozenset[str]] = {
    "deepseek": frozenset({"chat.deepseek.com"}),
    "doubao": frozenset({"doubao.com", "www.doubao.com"}),
    "tongyi": TONGYI_OFFICIAL_SHARE_HOSTS,
    "yiyan": YIYAN_OFFICIAL_SHARE_HOSTS,
}
_OFFICIAL_SHARE_UNSUPPORTED = frozenset({"yuanbao"})
_SHARE_AVAILABILITY = frozenset({"reachable", "redirected", "blocked", "unreachable", "unchecked"})
_SHARE_EMBED_STATUSES = frozenset({"allowed", "blocked", "unknown"})


def _official_share_url(value: object, platform: str) -> str | None:
    if not isinstance(value, str):
        return None
    url = _safe_http_url(value)
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in _OFFICIAL_SHARE_HOSTS.get(
        platform, frozenset()
    ):
        return None
    if platform == "deepseek" and not parsed.path.startswith("/share/"):
        return None
    if platform == "doubao" and not parsed.path.startswith("/thread/"):
        return None
    if platform == "tongyi" and re.fullmatch(r"/share/chat/[A-Fa-f0-9]{32}", parsed.path) is None:
        return None
    return url


def _share_optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("official share verification text is invalid")
    return value[:limit]


def _share_checked_at(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("official share checked_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("official share checked_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("official share checked_at must include timezone")
    return parsed


def _load_answer_share_manifest(
    evidence: list[CollectionEvidenceRef], platform: str
) -> dict[str, Any] | None:
    links = [item for item in evidence if item.relation_type == "official_share_link"]
    if not links:
        return None
    if len(links) != 1:
        raise ValueError("answer must have exactly one official share link")
    item = links[0]
    try:
        payload = json.loads(Path(item.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("official share manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("official share manifest is invalid")
    manifest_platform = str(payload.get("platform") or "").strip().lower()
    if manifest_platform != platform:
        raise ValueError("official share manifest platform mismatch")
    share_url = _official_share_url(payload.get("url"), platform)
    if share_url is None or share_url != item.source_url:
        raise ValueError("official share manifest URL is invalid")

    raw_verification = payload.get("verification")
    if raw_verification is None:
        raw_verification = {}
    if not isinstance(raw_verification, dict):
        raise ValueError("official share verification is invalid")
    availability = raw_verification.get("availability_status", "unchecked")
    embed_status = raw_verification.get("embed_status", "unknown")
    if availability not in _SHARE_AVAILABILITY or embed_status not in _SHARE_EMBED_STATUSES:
        raise ValueError("official share verification status is invalid")
    checked_at = _share_checked_at(raw_verification.get("checked_at"))
    http_status = raw_verification.get("http_status")
    if http_status is not None and (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 100 <= http_status <= 599
    ):
        raise ValueError("official share HTTP status is invalid")
    final_url_value = raw_verification.get("final_url", share_url)
    final_url = _official_share_url(final_url_value, platform) if final_url_value else None
    raw_redirects = raw_verification.get("redirect_chain", [])
    if not isinstance(raw_redirects, list) or len(raw_redirects) > 5:
        raise ValueError("official share redirect chain is invalid")
    redirects: list[dict[str, Any]] = []
    for raw in raw_redirects:
        if not isinstance(raw, dict):
            raise ValueError("official share redirect chain is invalid")
        from_url = _official_share_url(raw.get("from_url"), platform)
        to_url = _official_share_url(raw.get("to_url"), platform)
        redirect_status = raw.get("http_status")
        if (
            from_url is None
            or to_url is None
            or isinstance(redirect_status, bool)
            or redirect_status not in {301, 302, 303, 307, 308}
        ):
            raise ValueError("official share redirect chain is invalid")
        redirects.append({"from_url": from_url, "http_status": redirect_status, "to_url": to_url})
    content_hash = raw_verification.get("content_hash")
    if content_hash is not None and (
        not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash)
    ):
        raise ValueError("official share content hash is invalid")
    raw_allowlist = raw_verification.get("allowlist_valid")
    if raw_allowlist is not None and not isinstance(raw_allowlist, bool):
        raise ValueError("official share allowlist result is invalid")
    allowlist_valid = bool(final_url) and (checked_at is None or raw_allowlist is True)
    return {
        "availability_status": availability,
        "allowlist_valid": allowlist_valid,
        "checked_at": checked_at,
        "content_hash": content_hash,
        "csp_frame_ancestors": _share_optional_text(
            raw_verification.get("csp_frame_ancestors"), 1_000
        ),
        "embed_reason": _share_optional_text(raw_verification.get("embed_reason"), 1_000),
        "embed_status": embed_status,
        "failure_reason": _share_optional_text(raw_verification.get("failure_reason"), 1_000),
        "final_url": final_url,
        "http_status": http_status,
        "probe_version": _share_optional_text(raw_verification.get("probe_version"), 80)
        or "legacy-manifest-v1",
        "redirect_chain": redirects,
        "share_url": share_url,
        "x_frame_options": _share_optional_text(raw_verification.get("x_frame_options"), 500),
    }


def _persist_answer_share_artifact(
    *,
    session: Any,
    tenant_pub_id: str,
    project_pub_id: str,
    answer_pub_id: str,
    platform: str,
    evidence: list[CollectionEvidenceRef],
    evidence_ids_by_relation: dict[str, str],
) -> None:
    platform = platform.strip().lower()[:40]
    manifest = _load_answer_share_manifest(evidence, platform)
    if manifest is None:
        manifest = {
            "availability_status": "unchecked",
            "allowlist_valid": False,
            "checked_at": None,
            "content_hash": None,
            "csp_frame_ancestors": None,
            "embed_reason": "platform_share_unsupported"
            if platform in _OFFICIAL_SHARE_UNSUPPORTED
            else "official_share_missing",
            "embed_status": "unknown",
            "failure_reason": None,
            "final_url": None,
            "http_status": None,
            "probe_version": "unsupported-v1"
            if platform in _OFFICIAL_SHARE_UNSUPPORTED
            else "missing-v1",
            "redirect_chain": [],
            "share_url": None,
            "x_frame_options": None,
        }
    status = (
        "available"
        if manifest["share_url"] and manifest["allowlist_valid"]
        else "invalid"
        if manifest["share_url"]
        else "unsupported"
        if platform in _OFFICIAL_SHARE_UNSUPPORTED
        else "missing"
    )
    artifact_pub_id = "ash_" + sha256(f"{tenant_pub_id}|{answer_pub_id}".encode()).hexdigest()[:26]
    checked_at = manifest["checked_at"]
    last_accessible_at = (
        checked_at if manifest["availability_status"] in {"reachable", "redirected"} else None
    )
    session.execute(
        text(
            """
            INSERT INTO evidence.answer_share_artifact
              (pub_id,tenant_pub_id,project_pub_id,answer_pub_id,platform,status,share_url,
               final_url,redirect_chain,allowlist_valid,share_created_at,
               availability_status,http_status,checked_at,last_accessible_at,content_hash,
               embed_status,x_frame_options,csp_frame_ancestors,embed_reason,failure_reason,
               probe_version,share_link_evidence_pub_id,share_image_evidence_pub_id)
            VALUES
              (:pub_id,:tenant_pub_id,:project_pub_id,:answer_pub_id,:platform,:status,
               :share_url,:final_url,CAST(:redirect_chain AS jsonb),:allowlist_valid,
               :share_created_at,:availability_status,:http_status,:checked_at,
               :last_accessible_at,:content_hash,:embed_status,:x_frame_options,
               :csp_frame_ancestors,:embed_reason,:failure_reason,:probe_version,
               :share_link_evidence_pub_id,:share_image_evidence_pub_id)
            ON CONFLICT (tenant_pub_id,answer_pub_id) DO UPDATE SET
              project_pub_id=EXCLUDED.project_pub_id,
              platform=EXCLUDED.platform,
              status=EXCLUDED.status,
              share_url=EXCLUDED.share_url,
              final_url=EXCLUDED.final_url,
              redirect_chain=EXCLUDED.redirect_chain,
              allowlist_valid=EXCLUDED.allowlist_valid,
              share_created_at=COALESCE(
                evidence.answer_share_artifact.share_created_at,EXCLUDED.share_created_at),
              availability_status=EXCLUDED.availability_status,
              http_status=EXCLUDED.http_status,
              checked_at=EXCLUDED.checked_at,
              last_accessible_at=COALESCE(
                EXCLUDED.last_accessible_at,
                evidence.answer_share_artifact.last_accessible_at),
              content_hash=EXCLUDED.content_hash,
              embed_status=EXCLUDED.embed_status,
              x_frame_options=EXCLUDED.x_frame_options,
              csp_frame_ancestors=EXCLUDED.csp_frame_ancestors,
              embed_reason=EXCLUDED.embed_reason,
              failure_reason=EXCLUDED.failure_reason,
              probe_version=EXCLUDED.probe_version,
              share_link_evidence_pub_id=EXCLUDED.share_link_evidence_pub_id,
              share_image_evidence_pub_id=EXCLUDED.share_image_evidence_pub_id,
              updated_at=now()
            WHERE
              (EXCLUDED.share_url IS NOT NULL AND
               (evidence.answer_share_artifact.checked_at IS NULL OR
                (EXCLUDED.checked_at IS NOT NULL AND
                 EXCLUDED.checked_at >= evidence.answer_share_artifact.checked_at))) OR
              (evidence.answer_share_artifact.probe_version='legacy-backfill-v1' AND
               evidence.answer_share_artifact.share_url IS NULL)
            """
        ),
        {
            "pub_id": artifact_pub_id,
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "answer_pub_id": answer_pub_id,
            "platform": platform or "unknown",
            "status": status,
            "share_url": manifest["share_url"],
            "final_url": manifest["final_url"],
            "redirect_chain": json.dumps(
                manifest["redirect_chain"], sort_keys=True, separators=(",", ":")
            ),
            "allowlist_valid": manifest["allowlist_valid"],
            "share_created_at": checked_at,
            "availability_status": manifest["availability_status"],
            "http_status": manifest["http_status"],
            "checked_at": checked_at,
            "last_accessible_at": last_accessible_at,
            "content_hash": manifest["content_hash"],
            "embed_status": manifest["embed_status"],
            "x_frame_options": manifest["x_frame_options"],
            "csp_frame_ancestors": manifest["csp_frame_ancestors"],
            "embed_reason": manifest["embed_reason"],
            "failure_reason": manifest["failure_reason"],
            "probe_version": manifest["probe_version"],
            "share_link_evidence_pub_id": evidence_ids_by_relation.get("official_share_link"),
            "share_image_evidence_pub_id": evidence_ids_by_relation.get("official_share_image"),
        },
    )
    if checked_at is None:
        return
    event_key = "|".join(
        (
            artifact_pub_id,
            checked_at.isoformat(),
            manifest["availability_status"],
            manifest["final_url"] or "",
        )
    )
    event_pub_id = "sve_" + sha256(event_key.encode()).hexdigest()[:26]
    session.execute(
        text(
            """
            INSERT INTO evidence.answer_share_verification_event
              (pub_id,tenant_pub_id,artifact_pub_id,checked_at,availability_status,
               http_status,final_url,redirect_chain,allowlist_valid,content_hash,
               embed_status,x_frame_options,csp_frame_ancestors,embed_reason,
               failure_reason,probe_version)
            VALUES
              (:pub_id,:tenant_pub_id,:artifact_pub_id,:checked_at,:availability_status,
               :http_status,:final_url,CAST(:redirect_chain AS jsonb),:allowlist_valid,
               :content_hash,:embed_status,:x_frame_options,:csp_frame_ancestors,
               :embed_reason,:failure_reason,:probe_version)
            ON CONFLICT (pub_id) DO NOTHING
            """
        ),
        {
            "pub_id": event_pub_id,
            "tenant_pub_id": tenant_pub_id,
            "artifact_pub_id": artifact_pub_id,
            "checked_at": checked_at,
            "availability_status": manifest["availability_status"],
            "http_status": manifest["http_status"],
            "final_url": manifest["final_url"],
            "redirect_chain": json.dumps(
                manifest["redirect_chain"], sort_keys=True, separators=(",", ":")
            ),
            "allowlist_valid": manifest["allowlist_valid"],
            "content_hash": manifest["content_hash"],
            "embed_status": manifest["embed_status"],
            "x_frame_options": manifest["x_frame_options"],
            "csp_frame_ancestors": manifest["csp_frame_ancestors"],
            "embed_reason": manifest["embed_reason"],
            "failure_reason": manifest["failure_reason"],
            "probe_version": manifest["probe_version"],
        },
    )


def _destroy_production_account_key(
    tenant_pub_id: str, account_pub_id: str, profile_count: int
) -> bool:
    """Destroy the external account key before committing profile purge state."""
    settings = get_settings()
    if profile_count == 0 or settings.env.lower() not in {"production", "prod"}:
        return False
    if settings.kms_provider != "vault_transit" or not settings.vault_transit_deletion_token_file:
        raise KmsUnavailableError("external_deletion_authority_unavailable")
    deletion_authority = VaultTransitKms(
        settings.vault_transit_address,
        settings.vault_transit_deletion_token_file,
        settings.vault_transit_key_name,
    )
    deletion_authority.destroy_account_key(tenant_pub_id, account_pub_id)
    return True


@activity.defn
async def collect_with_adapter(item: CollectionTaskInput) -> CollectionTaskResult:
    """Fail-closed production adapter boundary.

    A worker deployment must replace this activity registration with a live,
    capability-gated platform adapter. Contract fixtures belong in tests only.
    """
    activity.heartbeat({"business_key": item.business_key, "stage": "adapter_started"})
    raise ApplicationError(
        "no live collection adapter is registered",
        type="adapter_not_configured",
        non_retryable=True,
    )


# INV-1 合格性判定的 geo 二元来源（2026-08-08 起）：平台→静态住宅中继出口省码，
# 由运营在 worker env ``GEO_MEASUREMENT_EXIT_GB_MAP`` 声明
# （``slug:6位GB,slug:6位GB``，与 GEO_REGION_GB_MAP 同格式）。中继上游租约在
# 采集时已验出口省码（wukong validate/probe），fidelity 与旧系统 lease 期观测
# 同级；换上游必须同步更新本映射（见 deploy/production/RESIDENT-BROWSERS.md）。
# 2026-08-09 起（浏览器矩阵化）键从平台 slug 升级为**实例键**
# （``doubao_sh:310000,tongyi_bj:110000`` 等，与 GEO_BROWSER_<KEY>_EXIT_GB
# 同值同真源）；解析逻辑不变——键本来就是 opaque token。
ENV_MEASUREMENT_EXIT_GB_MAP = "GEO_MEASUREMENT_EXIT_GB_MAP"


def _measurement_exit_gb_map() -> dict[str, str]:
    """解析 GEO_MEASUREMENT_EXIT_GB_MAP；畸形条目 fail-closed（不容忍半张图）。"""
    raw = os.environ.get(ENV_MEASUREMENT_EXIT_GB_MAP, "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for item in raw.split(","):
        slug, sep, gb = item.strip().partition(":")
        if not sep or not slug.strip() or not (gb.strip().isdigit() and len(gb.strip()) == 6):
            raise ApplicationError(
                f"{ENV_MEASUREMENT_EXIT_GB_MAP} must use slug:6-digit-gb entries",
                type="measurement_exit_gb_map_invalid",
                non_retryable=True,
            )
        result[slug.strip().lower()] = gb.strip()
    return result


def _measurement_geo_provenance(adapter: str, instance_key: str | None = None) -> dict[str, str]:
    """geo_source/observed_gb_code 二元：有声明出口→observed_gb_code；无声明→
    unverified（measurement_eligible 判不合格，宁缺不编造，INV-1 fail-loud）。

    查表顺序（2026-08-09 起，浏览器矩阵化）：① 实例键（batch 落库 matrix_json
    记录的实测常驻实例，最贴近真实出口）；② adapter slug（旧行为回退——
    per-task 老路径/历史负载没有实例记录；env 键升级为实例键后该回退只在
    仍保留 slug 键的旧部署命中）。两者都未命中 → unverified。"""
    mapping = _measurement_exit_gb_map()
    exit_gb: str | None = None
    if instance_key:
        exit_gb = mapping.get(instance_key.strip().lower())
    if exit_gb is None:
        exit_gb = mapping.get((adapter or "").strip().lower())
    if exit_gb is None:
        return {"geo_source": "unverified", "observed_gb_code": ""}
    return {"geo_source": "observed_gb_code", "observed_gb_code": exit_gb}


def _analysis_dimensions(
    task_input: CollectionTaskInput,
    *,
    run_pub_id: str,
    config_version_pub_id: str | None,
    browser_instance: str | None = None,
) -> dict[str, str]:
    """answer_analysis fanout 的 dimensions（含 INV-1 五元 provenance 盖章）。

    五元取值是 fanout 边界的结构事实：
    - captcha_mode=not_challenged：只有采集成功（state=completed）的答案才进入
      fanout——撞墙/撞码未解题一律 failed 落库、绝不进分析链；captcha-assist
      人工过码后从断点重采，最终答案产自过码后的干净会话（旧系统对应
      solved_as_human，两值同在合格集，判定结论不受影响）。
    - degraded_flag=0：同上边界保证（wall/incomplete/软限流信号题的答案根本
      不会持久化为 completed）。
    - account_source=self_pool：V2 测量账号=运营自有干净号（OTP 手工注入）。
    - rate_policy=pool_burn：V2 现行唯一采集策略（单常驻浏览器+固定地域中继+
      真人节奏串行）满足 pool_burn 语义，系统内不存在 burst 路径；将来引入
      第二策略时本值必须改由采集记录供给。
    - geo 二元见 _measurement_geo_provenance；``browser_instance`` = batch 落库
      matrix_json 记录的实测常驻实例键（2026-08-09 起），优先于 adapter slug
      查出口省码。
    """
    return {
        "query_text": task_input.query,
        "model": task_input.model,
        "region": task_input.region,
        "mode": task_input.mode,
        "channel": "api",
        "run_pub_id": run_pub_id,
        "config_version_pub_id": config_version_pub_id or "",
        "captcha_mode": "not_challenged",
        "degraded_flag": "0",
        "account_source": "self_pool",
        "rate_policy": "pool_burn",
        **_measurement_geo_provenance(task_input.adapter, browser_instance),
    }


def _persisted_task_input(
    task: CollectionTask,
    supplied: CollectionTaskInput | None,
) -> CollectionTaskInput | None:
    """Recover immutable collection dimensions without depending on one workflow generation.

    ``Continue-As-New`` only carries the remaining input slice.  The completed
    task row therefore has to be the source of truth when an old run reaches
    completion after deployment.  ``supplied`` remains the compatibility path
    for pre-matrix rows.
    """

    try:
        matrix = json.loads(task.matrix_json or "{}")
    except (TypeError, ValueError):
        matrix = {}
    required = ("query", "model", "region", "mode", "adapter")
    if isinstance(matrix, dict) and all(isinstance(matrix.get(key), str) for key in required):
        return CollectionTaskInput(
            business_key=task.business_key,
            query=matrix["query"],
            model=matrix["model"],
            region=matrix["region"],
            mode=matrix["mode"],
            adapter=matrix["adapter"],
        )
    return supplied


def _enqueue_answer_analysis(
    *,
    session: Any,
    tenant_pub_id: str,
    run: CollectionRun,
    project: Project,
    task: CollectionTask,
    task_input: CollectionTaskInput | None,
) -> str:
    """Create one answer-analysis job and command in the capture transaction.

    Raw answer text is deliberately absent from the command.  The analysis
    activity reloads it by ``capture_ref`` and verifies ``response_hash`` so a
    retry days later still analyzes the exact captured answer.
    """

    workflow_id = f"answer-analysis/{tenant_pub_id}/{run.pub_id}/{task.pub_id}"
    capture_ref = {
        "answer_pub_id": task.pub_id,
        "run_pub_id": run.pub_id,
        "business_key": task.business_key,
        "response_hash": task.response_hash or "",
    }
    config_version = session.get(MonitoringConfigVersion, run.config_version_id)
    brand = session.scalar(
        select(Brand)
        .where(Brand.project_id == run.project_id)
        .order_by(Brand.created_at, Brand.pub_id)
    )
    competitors = list(
        session.scalars(
            select(Competitor)
            .where(Competitor.project_id == run.project_id)
            .order_by(Competitor.created_at, Competitor.pub_id)
        )
    )

    if task_input is None or brand is None:
        reason = "missing_task_input" if task_input is None else "missing_brand"
        enqueue_analysis_job(
            session,
            tenant_pub_id=tenant_pub_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            run_id=run.id,
            answer_task_id=task.id,
            subject_type="answer",
            subject_pub_id=task.pub_id,
            analyzer_kind="answer_basic",
            policy_version=ANSWER_BASIC_POLICY_VERSION,
            input_hash=canonical_input_hash(
                {
                    "capture_ref": capture_ref,
                    "policy_version": ANSWER_BASIC_POLICY_VERSION,
                    "reason": reason,
                }
            ),
            workflow_id=workflow_id,
            state="not_requested" if task_input is None else "skipped",
            error_code=reason,
        )
        return reason

    analysis_context = {
        "brand": brand.name,
        "competitors": [item.name for item in competitors],
        "dimensions": _analysis_dimensions(
            task_input,
            run_pub_id=run.pub_id,
            config_version_pub_id=(config_version.pub_id if config_version is not None else None),
            browser_instance=(json.loads(task.matrix_json or "{}").get("browser_instance") or None),
        ),
        "own_domains": [brand.website] if brand.website else [],
        "adapter_version": task_input.adapter,
        "capture_time": task.created_at.astimezone(UTC).isoformat(),
        "channel": "api",
        "access_class": "customer_private",
    }
    analysis_contract = {
        "capture_ref": capture_ref,
        "analysis_context": analysis_context,
        "scorer_version": "scorer-v2",
        "metric_version": "metrics-v2",
        "model_version": "rules-v1",
        "policy_version": ANSWER_BASIC_POLICY_VERSION,
    }
    input_hash = canonical_input_hash(analysis_contract)
    job_pub_id = enqueue_analysis_job(
        session,
        tenant_pub_id=tenant_pub_id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        run_id=run.id,
        answer_task_id=task.id,
        subject_type="answer",
        subject_pub_id=task.pub_id,
        analyzer_kind="answer_basic",
        policy_version=ANSWER_BASIC_POLICY_VERSION,
        input_hash=input_hash,
        workflow_id=workflow_id,
    )
    payload = {
        "persist": True,
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project.pub_id,
        "answer_pub_id": task.pub_id,
        **analysis_contract,
        "analysis_job": {
            "pub_id": job_pub_id,
            "subject_type": "answer",
            "subject_pub_id": task.pub_id,
            "analyzer_kind": "answer_basic",
            "policy_version": ANSWER_BASIC_POLICY_VERSION,
        },
    }
    persisted_payload = session.execute(
        text(
            """
            INSERT INTO integration.workflow_start_command (
              command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,
              payload,trace_context
            ) VALUES (
              :command_id,:tenant_pub_id,'answer_analysis',:workflow_id,
              :task_queue,CAST(:payload AS jsonb),'{}'::jsonb
            )
            ON CONFLICT (workflow_id)
            DO UPDATE SET workflow_id=integration.workflow_start_command.workflow_id
            RETURNING payload
            """
        ),
        {
            "command_id": uuid.uuid4(),
            "tenant_pub_id": tenant_pub_id,
            "workflow_id": workflow_id,
            "task_queue": get_settings().analysis_temporal_task_queue,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        },
    ).scalar_one()
    if persisted_payload != payload:
        raise ApplicationError(
            "answer analysis workflow replay payload drifted",
            type="analysis_workflow_payload_drift",
            non_retryable=True,
        )
    return "enqueued"


def _enqueue_post_collection_analysis(
    *,
    session: Any,
    tenant_pub_id: str,
    run: CollectionRun,
    project: Project,
) -> str:
    """Queue run-level public-source and risk work after capture has terminated."""

    settings = get_settings()
    workflow_id = f"post-collection-analysis/{tenant_pub_id}/{run.pub_id}"
    config_version = session.get(MonitoringConfigVersion, run.config_version_id)
    source_analysis_profile = (
        session.execute(
            text(
                """
            SELECT pub_id,profile_hash,revision FROM platform.source_analysis_profile
            WHERE project_id=:project_id AND state='active'
            """
            ),
            {"project_id": run.project_id},
        )
        .mappings()
        .one_or_none()
    )
    source_analysis_profile_pub_id = (
        str(source_analysis_profile["pub_id"]) if source_analysis_profile is not None else None
    )
    source_analysis_profile_hash = (
        str(source_analysis_profile["profile_hash"])
        if source_analysis_profile is not None
        else None
    )
    page_inspection_model = (settings.audit_llm_model or settings.research_llm_model).strip()
    page_inspection_policy_version = (
        derive_page_inspection_version(
            profile_revision=int(source_analysis_profile["revision"]),
            model=page_inspection_model,
            prompt_version=PAGE_INSPECTION_PROMPT_VERSION,
        )
        if source_analysis_profile is not None
        else PAGE_INSPECTION_POLICY_VERSION
    )
    contract = {
        "tenant_pub_id": tenant_pub_id,
        "project_pub_id": project.pub_id,
        "run_pub_id": run.pub_id,
        "config_version_pub_id": (config_version.pub_id if config_version is not None else ""),
        "policy_version": POST_COLLECTION_POLICY_VERSION,
        "source_task_queue": settings.source_temporal_task_queue,
        # Freeze an immutable profile revision at handoff time.  A profile
        # created later must trigger a new analysis version; it must not alter
        # the meaning of an already queued job.
        "source_analysis_profile_pub_id": source_analysis_profile_pub_id,
        "source_analysis_profile_hash": source_analysis_profile_hash,
        "page_inspection_policy_version": page_inspection_policy_version,
        "page_inspection_model": page_inspection_model,
        "page_inspection_prompt_version": PAGE_INSPECTION_PROMPT_VERSION,
    }
    for analyzer_kind in RUN_ANALYZER_KINDS:
        enqueue_analysis_job(
            session,
            tenant_pub_id=tenant_pub_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            run_id=run.id,
            answer_task_id=None,
            subject_type="run",
            subject_pub_id=run.pub_id,
            analyzer_kind=analyzer_kind,
            policy_version=POST_COLLECTION_POLICY_VERSION,
            input_hash=canonical_input_hash({**contract, "analyzer_kind": analyzer_kind}),
            workflow_id=workflow_id,
            state=(
                "not_requested"
                if analyzer_kind == "page_inspection" and source_analysis_profile_pub_id is None
                else "queued"
            ),
            error_code=(
                "profile_missing"
                if analyzer_kind == "page_inspection" and source_analysis_profile_pub_id is None
                else None
            ),
        )
    payload = {
        **contract,
        "analysis_jobs": list(RUN_ANALYZER_KINDS),
    }
    persisted_payload = session.execute(
        text(
            """
            INSERT INTO integration.workflow_start_command (
              command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,
              payload,trace_context
            ) VALUES (
              :command_id,:tenant_pub_id,'post_collection_analysis',:workflow_id,
              :task_queue,CAST(:payload AS jsonb),'{}'::jsonb
            )
            ON CONFLICT (workflow_id)
            DO UPDATE SET workflow_id=integration.workflow_start_command.workflow_id
            RETURNING payload
            """
        ),
        {
            "command_id": uuid.uuid4(),
            "tenant_pub_id": tenant_pub_id,
            "workflow_id": workflow_id,
            "task_queue": settings.analysis_temporal_task_queue,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        },
    ).scalar_one()
    if persisted_payload != payload:
        raise ApplicationError(
            "post-collection workflow replay payload drifted",
            type="post_collection_workflow_payload_drift",
            non_retryable=True,
        )
    return "enqueued"


def _persist_answer_capture_event(
    *,
    session: Any,
    tenant_pub_id: str,
    project_pub_id: str,
    run: CollectionRun,
    task: CollectionTask,
) -> str:
    payload = {
        "answer_pub_id": task.pub_id,
        "project_pub_id": project_pub_id,
        "run_pub_id": run.pub_id,
        "business_key": task.business_key,
        "capture_state": "completed",
        "quality_state": task.quality_state,
        "response_hash": task.response_hash,
    }
    persisted = (
        session.execute(
            text(
                """
            INSERT INTO integration.outbox_event (
              event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
              occurred_at
            ) VALUES (
              :event_id,:tenant_pub_id,'answer.capture.completed',:answer_pub_id,
              :trace_id,CAST(:payload AS jsonb),:occurred_at
            )
            ON CONFLICT (tenant_pub_id,aggregate_pub_id)
              WHERE event_type='answer.capture.completed'
            DO UPDATE SET event_id=integration.outbox_event.event_id
            RETURNING event_id,payload
            """
            ),
            {
                "event_id": new_pub_id("evt"),
                "tenant_pub_id": tenant_pub_id,
                "answer_pub_id": task.pub_id,
                "trace_id": sha256(f"{run.workflow_id}|{task.pub_id}".encode()).hexdigest(),
                "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "occurred_at": task.created_at,
            },
        )
        .mappings()
        .one()
    )
    if persisted["payload"] != payload:
        raise ApplicationError(
            "answer capture event replay payload drifted",
            type="answer_capture_event_payload_drift",
            non_retryable=True,
        )
    return str(persisted["event_id"])


def _stable_uuid(value: str) -> uuid.UUID:
    """Deterministic relational identity for idempotent capture retries."""

    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-platform-v2:{value}")


def _stable_pub_id(prefix: str, value: str) -> str:
    return f"{prefix}_{sha256(value.encode()).hexdigest()[:26]}"


def _persist_uvw_facts(
    *,
    session: Any,
    run: CollectionRun,
    project: Project,
    task: CollectionTask,
    retrieval_events: list[dict[str, Any]],
    evidence_ids_by_relation: dict[str, str],
    allow_legacy_identity: bool = False,
) -> None:
    """Persist every U occurrence and its observed V/final-reference state.

    Identity upserts are allowed for sites and normalized URLs.  Retrieval
    events and occurrences are immutable answer-capture facts: a Temporal retry
    must reproduce the same rows byte-for-byte or fail loudly as payload drift.
    """

    event_id_by_ordinal: dict[int, uuid.UUID] = {}
    for event in retrieval_events:
        ordinal = int(event["ordinal"])
        stable_key = f"{task.pub_id}|retrieval|{ordinal}"
        event_id = _stable_uuid(stable_key)
        event_pub_id = _stable_pub_id("ret", stable_key)
        evidence_pub_id = evidence_ids_by_relation.get(event.get("evidence_relation") or "")
        queries_json = json.dumps(event["queries"], ensure_ascii=False, separators=(",", ":"))
        persisted = (
            session.execute(
                text(
                    """
                    INSERT INTO platform.answer_retrieval_event
                      (id,pub_id,tenant_id,project_id,run_id,answer_task_id,ordinal,
                       queries,u_observation,v_observation,final_reference_observation,
                       evidence_pub_id,created_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:project_id,:run_id,:answer_task_id,:ordinal,
                       CAST(:queries AS jsonb),:u_observation,:v_observation,
                       :final_observation,:evidence_pub_id,:created_at)
                    ON CONFLICT (answer_task_id,ordinal) DO NOTHING
                    RETURNING id,pub_id,queries,u_observation,v_observation,
                              final_reference_observation,evidence_pub_id
                    """
                ),
                {
                    "id": event_id,
                    "pub_id": event_pub_id,
                    "tenant_id": run.tenant_id,
                    "project_id": project.id,
                    "run_id": run.id,
                    "answer_task_id": task.id,
                    "ordinal": ordinal,
                    "queries": queries_json,
                    "u_observation": event["u_observation"],
                    "v_observation": event["v_observation"],
                    "final_observation": event["final_reference_observation"],
                    "evidence_pub_id": evidence_pub_id,
                    "created_at": task.created_at,
                },
            )
            .mappings()
            .one_or_none()
        )
        if persisted is None:
            persisted = (
                session.execute(
                    text(
                        """
                        SELECT id,pub_id,queries,u_observation,v_observation,
                               final_reference_observation,evidence_pub_id
                        FROM platform.answer_retrieval_event
                        WHERE answer_task_id=:answer_task_id AND ordinal=:ordinal
                        """
                    ),
                    {"answer_task_id": task.id, "ordinal": ordinal},
                )
                .mappings()
                .one()
            )
        expected_event = {
            "pub_id": event_pub_id,
            "queries": event["queries"],
            "u_observation": event["u_observation"],
            "v_observation": event["v_observation"],
            "final_reference_observation": event["final_reference_observation"],
            "evidence_pub_id": evidence_pub_id,
        }
        if {key: persisted[key] for key in expected_event} != expected_event:
            raise ApplicationError(
                "retrieval event replay payload drifted",
                type="retrieval_event_payload_drift",
                non_retryable=True,
            )
        event_id_by_ordinal[ordinal] = persisted["id"]

    for occurrence in occurrence_rows(retrieval_events):
        if allow_legacy_identity:
            historical = (
                session.execute(
                    text(
                        """
                        SELECT raw_url,u_state,u_rank,v_state,v_open_order,
                               final_reference_state,final_reference_ordinal,w_state,
                               title,summary
                        FROM platform.answer_source_occurrence
                        WHERE answer_task_id=:answer_task_id
                          AND occurrence_ordinal=:occurrence_ordinal
                        """
                    ),
                    {
                        "answer_task_id": task.id,
                        "occurrence_ordinal": occurrence.occurrence_ordinal,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if historical is not None:
                expected_historical = {
                    "raw_url": occurrence.raw_url,
                    "u_state": occurrence.u_state,
                    "u_rank": occurrence.u_rank,
                    "v_state": occurrence.v_state,
                    "v_open_order": occurrence.v_open_order,
                    "final_reference_state": occurrence.final_reference_state,
                    "final_reference_ordinal": occurrence.final_reference_ordinal,
                    "title": occurrence.title,
                    "summary": occurrence.summary,
                }
                if {key: historical[key] for key in expected_historical} != expected_historical:
                    raise ApplicationError(
                        "historical source occurrence replay payload drifted",
                        type="source_occurrence_payload_drift",
                        non_retryable=True,
                    )
                continue
        site_key = f"{run.tenant_id}|{occurrence.host}"
        site_row = (
            session.execute(
                text(
                    """
                    INSERT INTO platform.source_site
                      (id,pub_id,tenant_id,host,created_at,updated_at)
                    VALUES (:id,:pub_id,:tenant_id,:host,:captured_at,:captured_at)
                    ON CONFLICT (tenant_id,host)
                    DO UPDATE SET updated_at=GREATEST(
                      platform.source_site.updated_at,EXCLUDED.updated_at)
                    RETURNING id
                    """
                ),
                {
                    "id": _stable_uuid(site_key),
                    "pub_id": _stable_pub_id("sit", site_key),
                    "tenant_id": run.tenant_id,
                    "host": occurrence.host,
                    "captured_at": task.created_at,
                },
            )
            .mappings()
            .one()
        )
        canonical_hash = sha256(occurrence.canonical_url.encode()).hexdigest()
        url_key = f"{run.tenant_id}|{canonical_hash}|{occurrence.canonical_url}"
        url_row = (
            session.execute(
                text(
                    """
                    INSERT INTO platform.source_url
                      (id,pub_id,tenant_id,site_id,canonical_url,canonical_url_hash,
                       normalization_version,first_raw_url,created_at,updated_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:site_id,:canonical_url,:canonical_hash,
                       :normalization_version,:raw_url,:captured_at,:captured_at)
                    ON CONFLICT (tenant_id,canonical_url_hash,canonical_url)
                    DO UPDATE SET updated_at=GREATEST(
                      platform.source_url.updated_at,EXCLUDED.updated_at)
                    RETURNING id
                    """
                ),
                {
                    "id": _stable_uuid(url_key),
                    "pub_id": _stable_pub_id("url", url_key),
                    "tenant_id": run.tenant_id,
                    "site_id": site_row["id"],
                    "canonical_url": occurrence.canonical_url,
                    "canonical_hash": canonical_hash,
                    "normalization_version": URL_NORMALIZATION_VERSION,
                    "raw_url": occurrence.raw_url,
                    "captured_at": task.created_at,
                },
            )
            .mappings()
            .one()
        )
        occurrence_key = f"{task.pub_id}|occurrence|{occurrence.occurrence_ordinal}"
        event_ordinal = occurrence.retrieval_event_ordinal
        occurrence_event_id = (
            event_id_by_ordinal.get(event_ordinal) if event_ordinal is not None else None
        )
        occurrence_event: dict[str, Any] | None = None
        for candidate_event in retrieval_events:
            if candidate_event["ordinal"] == occurrence.retrieval_event_ordinal:
                occurrence_event = candidate_event
                break
        evidence_pub_id = evidence_ids_by_relation.get(
            (occurrence_event or {}).get("evidence_relation") or ""
        )
        values = {
            "id": _stable_uuid(occurrence_key),
            "pub_id": _stable_pub_id("uoc", occurrence_key),
            "tenant_id": run.tenant_id,
            "project_id": project.id,
            "run_id": run.id,
            "answer_task_id": task.id,
            "retrieval_event_id": occurrence_event_id,
            "source_url_id": url_row["id"],
            "occurrence_ordinal": occurrence.occurrence_ordinal,
            "query_text": occurrence.query,
            "raw_url": occurrence.raw_url,
            "u_state": occurrence.u_state,
            "u_rank": occurrence.u_rank,
            "v_state": occurrence.v_state,
            "v_open_order": occurrence.v_open_order,
            "final_reference_state": occurrence.final_reference_state,
            "final_reference_ordinal": occurrence.final_reference_ordinal,
            "w_state": occurrence.w_state,
            "title": occurrence.title,
            "summary": occurrence.summary,
            "evidence_pub_id": evidence_pub_id,
            "captured_at": task.created_at,
        }
        persisted = (
            session.execute(
                text(
                    """
                    INSERT INTO platform.answer_source_occurrence
                      (id,pub_id,tenant_id,project_id,run_id,answer_task_id,
                       retrieval_event_id,source_url_id,occurrence_ordinal,query_text,
                       raw_url,u_state,u_rank,v_state,v_open_order,final_reference_state,
                       final_reference_ordinal,w_state,title,summary,evidence_pub_id,
                       captured_at,created_at)
                    VALUES
                      (:id,:pub_id,:tenant_id,:project_id,:run_id,:answer_task_id,
                       :retrieval_event_id,:source_url_id,:occurrence_ordinal,:query_text,
                       :raw_url,:u_state,:u_rank,:v_state,:v_open_order,
                       :final_reference_state,:final_reference_ordinal,:w_state,:title,
                       :summary,:evidence_pub_id,:captured_at,:captured_at)
                    ON CONFLICT (answer_task_id,occurrence_ordinal) DO NOTHING
                    RETURNING pub_id,retrieval_event_id,source_url_id,query_text,raw_url,
                              u_state,u_rank,v_state,v_open_order,final_reference_state,
                              final_reference_ordinal,w_state,title,summary,evidence_pub_id
                    """
                ),
                values,
            )
            .mappings()
            .one_or_none()
        )
        comparison_keys = (
            "pub_id",
            "retrieval_event_id",
            "source_url_id",
            "query_text",
            "raw_url",
            "u_state",
            "u_rank",
            "v_state",
            "v_open_order",
            "final_reference_state",
            "final_reference_ordinal",
            "title",
            "summary",
            "evidence_pub_id",
        )
        if persisted is None:
            persisted = (
                session.execute(
                    text(
                        """
                        SELECT pub_id,retrieval_event_id,source_url_id,query_text,raw_url,
                               u_state,u_rank,v_state,v_open_order,final_reference_state,
                               final_reference_ordinal,w_state,title,summary,evidence_pub_id
                        FROM platform.answer_source_occurrence
                        WHERE answer_task_id=:answer_task_id
                          AND occurrence_ordinal=:occurrence_ordinal
                        """
                    ),
                    values,
                )
                .mappings()
                .one()
            )
        if {key: persisted[key] for key in comparison_keys} != {
            key: values[key] for key in comparison_keys
        }:
            raise ApplicationError(
                "source occurrence replay payload drifted",
                type="source_occurrence_payload_drift",
                non_retryable=True,
            )


@activity.defn
def publish_downstream_event(
    run_pub_id: str,
    tenant_pub_id: str | None = None,
    task_inputs: list[CollectionTaskInput] | None = None,
    enqueue_post_collection: bool = False,
) -> str:
    """Persist the collection completion event exactly once.

    ``tenant_pub_id=None`` preserves replay compatibility for histories created
    before the durable-outbox workflow patch. Migration s04_0022 backfills
    already-completed runs from that history.
    """
    if tenant_pub_id is None:
        return f"collection.completed:{run_pub_id}"
    try:
        activity.heartbeat({"run_pub_id": run_pub_id, "stage": "outbox"})
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        workflow_id = f"collection/{tenant_pub_id}/{run_pub_id}"
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.pub_id == run_pub_id).with_for_update()
        )
        if run is None:
            raise ApplicationError("collection run not found", type="run_not_found")
        if run.state not in {"completed", "completed_with_failures"}:
            raise ApplicationError(
                "collection run is not complete",
                type="run_not_completed",
                non_retryable=True,
            )
        event_id = session.execute(
            text(
                """
                INSERT INTO integration.outbox_event
                  (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
                   occurred_at)
                VALUES
                  (:event_id,:tenant_pub_id,'collection.run.completed',:run_pub_id,:trace_id,
                   CAST(:payload AS jsonb),:occurred_at)
                ON CONFLICT (tenant_pub_id,aggregate_pub_id)
                  WHERE event_type='collection.run.completed'
                DO UPDATE SET event_id=integration.outbox_event.event_id
                RETURNING event_id
                """
            ),
            {
                "event_id": new_pub_id("evt"),
                "tenant_pub_id": tenant_pub_id,
                "run_pub_id": run_pub_id,
                "trace_id": sha256(workflow_id.encode()).hexdigest(),
                "payload": json.dumps(
                    {
                        "run_pub_id": run_pub_id,
                        "workflow_id": run.workflow_id,
                        "state": run.state,
                        "total_tasks": run.total_tasks,
                        "completed_tasks": run.completed_tasks,
                        "failed_tasks": run.failed_tasks,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "occurred_at": datetime.now(UTC),
            },
        ).scalar_one()
        project = session.get(Project, run.project_id)
        if project is None:
            raise ApplicationError("collection project not found", type="project_not_found")
        completed = list(
            session.scalars(
                select(CollectionTask)
                .where(CollectionTask.run_id == run.id, CollectionTask.state == "completed")
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        task_by_key = {item.business_key: item for item in task_inputs or []}
        # Compatibility reconciliation only. New captures enqueue in
        # persist_collection_result, before the next browser question starts.
        if task_inputs is not None:
            for task in completed:
                if task.answer_text is None:
                    continue
                _enqueue_answer_analysis(
                    session=session,
                    tenant_pub_id=tenant_pub_id,
                    run=run,
                    project=project,
                    task=task,
                    task_input=_persisted_task_input(task, task_by_key.get(task.business_key)),
                )
        analysis_expected = len(completed)
        analysis_commands = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM platform.analysis_job job
                    JOIN integration.workflow_start_command command
                      ON command.tenant_pub_id=:tenant_pub_id
                     AND command.workflow_type='answer_analysis'
                     AND command.workflow_id=job.workflow_id
                    WHERE job.run_id=:run_id
                      AND job.subject_type='answer'
                      AND job.analyzer_kind='answer_basic'
                    """
                ),
                {
                    "tenant_pub_id": tenant_pub_id,
                    "run_id": run.id,
                },
            ).scalar_one()
        )
        if analysis_expected == 0:
            analysis_admission = "missing_completed_answers"
        elif analysis_commands == analysis_expected:
            analysis_admission = "enqueued"
        else:
            analysis_admission = "partial_fanout"
        post_analysis_admission = "not_requested"
        if enqueue_post_collection:
            post_analysis_admission = _enqueue_post_collection_analysis(
                session=session,
                tenant_pub_id=tenant_pub_id,
                run=run,
                project=project,
            )
        else:
            # A compatibility/manual replay must not make an already durable
            # handoff look as though it was never requested.
            existing_post_command = session.execute(
                text(
                    """
                    SELECT 1 FROM integration.workflow_start_command
                    WHERE tenant_pub_id=:tenant_pub_id
                      AND workflow_type='post_collection_analysis'
                      AND workflow_id=:workflow_id
                    """
                ),
                {
                    "tenant_pub_id": tenant_pub_id,
                    "workflow_id": (f"post-collection-analysis/{tenant_pub_id}/{run.pub_id}"),
                },
            ).first()
            if existing_post_command is not None:
                post_analysis_admission = "enqueued"
        session.execute(
            text(
                """
                UPDATE integration.outbox_event
                SET payload=payload || CAST(:admission AS jsonb)
                WHERE event_id=:event_id
                """
            ),
            {
                "event_id": event_id,
                "admission": json.dumps(
                    {
                        "analysis_admission": (analysis_admission),
                        "analysis_commands": analysis_commands,
                        "analysis_expected": analysis_expected,
                        "post_analysis_admission": post_analysis_admission,
                    },
                    separators=(",", ":"),
                ),
            },
        )
        session.commit()
    return f"collection.completed:{run_pub_id}:{event_id}"


@activity.defn
def mark_collection_run_terminal(
    tenant_pub_id: str, run_pub_id: str, state: str, error_code: str | None
) -> None:
    if state not in {"completed", "cancelled", "failed"}:
        raise ApplicationError(
            "invalid collection terminal state",
            type="invalid_collection_terminal_state",
            non_retryable=True,
        )
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.pub_id == run_pub_id).with_for_update()
        )
        if run is None:
            raise ApplicationError("collection run not found", type="run_not_found")
        pending_tasks = list(
            session.scalars(
                select(CollectionTask)
                .where(CollectionTask.run_id == run.id, CollectionTask.state == "pending")
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
                .with_for_update()
            )
        )
        pending_error = (
            error_code or ("workflow_cancelled" if state == "cancelled" else "query_result_missing")
        )[:40]
        terminal_at = datetime.now(UTC)
        for task in pending_tasks:
            task.state = "failed"
            task.attempt_count = max(task.attempt_count, 1)
            task.terminal_at = terminal_at
            task.quality_state = pending_error
            task.answer_text = None
            task.citations_json = "[]"
            task.evidence_json = json.dumps(
                [
                    {
                        "kind": "failure_record",
                        "status": "not_executed",
                        "error_type": pending_error,
                        "message": "workflow terminated before this query produced a result",
                    }
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
            task.search_queries_json = "[]"
            run.failed_tasks += 1
            record_query_attempt(
                session,
                run=run,
                task=task,
                outcome="failed",
                error_code=pending_error,
                execution_status="not_executed",
            )
            record_query_failure_knowledge(
                session,
                run=run,
                task=task,
                error_code=pending_error,
                execution_status="not_executed",
            )
            mark_source_retry_outcome(
                session,
                run=run,
                business_key=task.business_key,
                succeeded=False,
                error_code=pending_error,
            )
        # Completion written by persist_collection_result is already terminal;
        # retries must not demote it. completed_with_failures 同属 s04_0019 终态
        # 词表（触发器 ck_collection_run_terminal_state 冻结），同样不得改写。
        # 与触发器 ck_collection_run_terminal_state 词表严格对齐（含 skipped）：
        # 词表不一致 = reconcile/收尾 UPDATE 反复撞 23514 毒循环（20260806 生产实证）。
        terminal_states = {"completed", "completed_with_failures", "cancelled", "failed", "skipped"}
        if run.state not in terminal_states:
            derived = _derive_run_state(run)
            if state == "completed" and derived in {"completed", "completed_with_failures"}:
                run.state = derived
            else:
                run.state = state
            run.error_code = error_code or (pending_error if pending_tasks else None)
        project = session.get(Project, run.project_id)
        if project is None:
            raise ApplicationError("collection project not found", type="project_not_found")

        # Reconcile ledgers for legacy captures too.  Inserts are deterministic
        # and append-only, so activity retries cannot duplicate learning rows.
        terminal_tasks = list(
            session.scalars(
                select(CollectionTask)
                .where(CollectionTask.run_id == run.id)
                .order_by(CollectionTask.created_at, CollectionTask.pub_id)
            )
        )
        for task in terminal_tasks:
            if task.state == "completed":
                record_query_attempt(
                    session,
                    run=run,
                    task=task,
                    outcome="succeeded",
                    error_code=None,
                    execution_status="ok",
                )
                mark_source_retry_outcome(
                    session,
                    run=run,
                    business_key=task.business_key,
                    succeeded=True,
                )
            elif task.state == "failed":
                task_error = task.quality_state or "query_failed"
                record_query_attempt(
                    session,
                    run=run,
                    task=task,
                    outcome="failed",
                    error_code=task_error,
                    execution_status="failed",
                )
                record_query_failure_knowledge(
                    session,
                    run=run,
                    task=task,
                    error_code=task_error,
                    execution_status="failed",
                )
                mark_source_retry_outcome(
                    session,
                    run=run,
                    business_key=task.business_key,
                    succeeded=False,
                    error_code=task_error,
                )

        has_failures = any(task.state == "failed" for task in terminal_tasks)
        if has_failures or run.state in {"failed", "cancelled", "completed_with_failures"}:
            record_run_failure_knowledge(
                session,
                run=run,
                error_code=(
                    run.error_code
                    or ("completed_with_query_failures" if has_failures else f"run_{run.state}")
                ),
            )
        # Successful queries are useful even when siblings fail.  Preserve the
        # per-answer fanout and also admit run-level analysis once all pending
        # queries have been made explicit terminal records.
        if run.completed_tasks > 0:
            _enqueue_post_collection_analysis(
                session=session,
                tenant_pub_id=tenant_pub_id,
                run=run,
                project=project,
            )

        # Browser adapters intentionally resolve every one-item Activity.  The
        # governor therefore reserves one account for the whole run; release
        # that reservation only at the workflow's real terminal activity.  A
        # wall state (captcha/muted/quota/error) is not repaired here: only a
        # still-running reservation becomes idle.
        AccountGovernor(session).release_run_reservations(run_pub_id=run_pub_id)

        if has_failures and run.state != "cancelled":
            from geo_platform.collection.run_service import stage_collection_run

            config = session.get(MonitoringConfigVersion, run.config_version_id)
            if config is None:
                raise ApplicationError(
                    "collection config not found",
                    type="config_version_not_found",
                )
            try:
                stage_collection_run(
                    session,
                    tenant_id=run.tenant_id,
                    tenant_pub_id=tenant_pub_id,
                    project_pub_id=project.pub_id,
                    config_version_pub_id=config.pub_id,
                    idempotency_key=f"auto-query-retry:{run.pub_id}",
                    initiated_by_pub_id=run.initiated_by_pub_id or "system_auto_retry",
                    source="retry",
                    retry_of_run_pub_id=run.pub_id,
                    retry_trigger="automatic",
                )
            except ValueError as exc:
                retry_error = str(exc)
                if retry_error not in {
                    "retry_auto_exhausted",
                    "retry_has_no_failed_queries",
                    "retry_intent_claim_conflict",
                } and not retry_error.startswith("retry_source_matrix_"):
                    raise
        session.commit()


def _task_matrix(
    task_input: CollectionTaskInput | None,
    browser_instance: str | None = None,
) -> dict[str, str]:
    """collection_task.matrix_json 的 dict 真源（ok/失败两条 persist 路径共用）。

    ``browser_instance``（2026-08-09 起，浏览器矩阵化）：batch 实际使用的常驻
    实例键，fanout 的 INV-1 geo provenance 优先按它查出口省码。None 时整个键
    **不写出**——旧 payload 逐字节不变（persist 的 replay drift 检查零漂移）。
    """
    if task_input is None:
        return {}
    matrix = {
        "query": task_input.query,
        "model": task_input.model,
        "region": task_input.region,
        "mode": task_input.mode,
        "adapter": task_input.adapter,
    }
    if browser_instance:
        matrix["browser_instance"] = browser_instance
    return matrix


# ---------------------------------------------------------------------------
# 采集账号治理上报（2026-08-14 起，设计文档 caiji-0813 §5.4 统一出口 / §6.1）
# ---------------------------------------------------------------------------

# 治理墙词表 = account_governor._WALL_TYPES 的同名词集（墙 error_type 原样透传为
# governor wall_type）。mode_unconfirmed/deep_think_toggle_failed 等题级失败
# 不在表内 → 只记 task_outcome（参与同类失败熔断），不报墙。
_GOVERNOR_WALL_ERROR_TYPES = frozenset({"wall_quota", "wall_muted", "wall_captcha", "wall_refusal"})

# adapter 墙 outcome 的 error_message 携带禁言解封点（wall_lexicon WallVerdict.until
# 的 isoformat，naive 本地时间=Asia/Shanghai，精确到分）：两种 producer 形状
# 「answer-text wall hit [wall_muted] … until=2026-08-14T13:02:00 fragment=…」与
# 「muted banner on page (…) until=…; evidence=…」。
_MUTED_UNTIL_RE = re.compile(r"\buntil=(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _governor_wall_type(status: str, error_type: str | None) -> str | None:
    """墙 outcome → governor wall_type；非墙/非治理词表 → None（只记 task_outcome）。"""
    if status == "wall" and error_type in _GOVERNOR_WALL_ERROR_TYPES:
        return error_type
    return None


def _parse_wall_until(error_message: str | None) -> datetime | None:
    """从墙 outcome 的 error_message 解析禁言解封点（naive 北京 → aware UTC）。

    解析不出 → None（governor 按 muted_until=NULL=人工封禁语义处理，不自动恢复）。
    """
    if not error_message:
        return None
    match = _MUTED_UNTIL_RE.search(error_message)
    if match is None:
        return None
    try:
        naive = datetime.fromisoformat(match.group(1))
    except ValueError:
        return None
    return naive.replace(tzinfo=_SHANGHAI).astimezone(UTC)


def _report_outcome_to_governor(
    *,
    run_pub_id: str,
    result: CollectionBatchItemResult | CollectionTaskResult,
    task_input: CollectionTaskInput | None,
    task_pub_id: str,
    status: str,
) -> None:
    """采集终态的治理旁路上报：逐题 task_outcome + 墙 outcome 的 report_wall。

    - 逐题 record_task_outcome（task_pub_id 入去重键，activity 重试/重放不重复
      计数）：ok→success 记用量台账；失败按 status 词（wall/incomplete/aborted）
      + error_type 参与同类失败 ≥3 熔断。
    - 墙 outcome（wall_quota/wall_muted/wall_captcha/wall_refusal）→ report_wall：
      quota 传 mode、until=None（governor 自算日重置点）；muted 从 error_message
      解析解封点；refusal 只记事件不改状态（governor 内语义）。
    - 治理层是旁路：独立 WorkerSessionLocal 事务 + 全异常吞为 warning——治理
      故障绝不阻断/毒化采集落库主链（与 browser_router 的 fail-open 同款）。
      GEO_ACCOUNT_GOVERNANCE=off 时整体跳过（单测/应急 kill switch）。
    只在 persist 新建任务行（非幂等重放）时调用，重试不重复报墙。
    """
    if task_input is None or not account_governance_enabled():
        return
    platform = (task_input.adapter or "").strip().lower()
    if not platform:
        return
    error_type = getattr(result, "error_type", None)
    error_message = getattr(result, "error_message", None)
    browser_instance_key = getattr(result, "browser_instance", None)
    mode = task_input.mode or None
    try:
        with WorkerSessionLocal() as session:
            governor = AccountGovernor(session)
            governor.record_task_outcome(
                platform=platform,
                browser_instance_key=browser_instance_key,
                outcome="success" if status == "ok" else status,
                error_type=error_type,
                run_pub_id=run_pub_id,
                mode=mode,
                task_pub_id=task_pub_id,
            )
            wall_type = _governor_wall_type(status, error_type)
            if wall_type is not None:
                governor.report_wall(
                    platform=platform,
                    wall_type=wall_type,
                    evidence=(error_message or error_type or "")[:500],
                    browser_instance_key=browser_instance_key,
                    run_pub_id=run_pub_id,
                    mode=mode,
                    until=(_parse_wall_until(error_message) if wall_type == "wall_muted" else None),
                )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — 治理旁路故障不阻断采集主链
        log.warning(
            "account_governor_report_failed",
            run_pub_id=run_pub_id,
            task_pub_id=task_pub_id,
            platform=platform,
            status=status,
            error_type=error_type,
            report_error_type=type(exc).__name__,
        )


@activity.defn
def persist_collection_result(
    tenant_pub_id: str,
    run_pub_id: str,
    result: CollectionBatchItemResult | CollectionTaskResult,
    task_input: CollectionTaskInput | None = None,
) -> None:
    """Transactional, business-key idempotent activity.

    ``result`` 兼容两种 producer：per-task 老路径的 CollectionTaskResult
    （无 status 字段——按 ``"ok"`` 处理，行为与旧形状完全一致；经 Temporal
    序列化往返的批次结果也会按参数类型补齐默认值）与 collect_doubao_batch
    的 per-item CollectionBatchItemResult。
    ``status != "ok"`` 的题（wall/incomplete/aborted）走失败落库：诚实记
    state="failed"，绝不出现在答案/证据链路（INV-32 零合成）。
    """
    status = getattr(result, "status", None) or "ok"
    if status not in COLLECTION_BATCH_ITEM_STATUSES:
        raise ApplicationError(
            f"unknown collection result status: {status!r}",
            type="collection_result_status_unknown",
            non_retryable=True,
        )
    if status != "ok":
        # 只有 batch per-item 结果才会携带非 ok 状态（老形状无 status 必走 ok 分支）。
        assert isinstance(result, CollectionBatchItemResult)
        _persist_collection_failure(tenant_pub_id, run_pub_id, result, task_input, status)
        return
    # 原始采集原则（2026-08-06 用户拍板）：answer_text/citations/search_queries
    # 等公开平台输出是测量原料，原文存储、零 DLP；结构校验（URL/长度/形状）保留。
    # screenshot_ref 是平台自产路径串（非公开内容），保持 fail-closed 自检。
    try:
        if result.screenshot_ref:
            _assert_system_ref_secret_free(result.screenshot_ref)
    except ValueError as error:
        raise ApplicationError(
            "collection result rejected by DLP",
            type="collection_result_dlp_rejected",
            non_retryable=True,
        ) from error
    try:
        raw_answer = result.answer_text if isinstance(result.answer_text, str) else ""
        citations = _normalize_citations(
            list(getattr(result, "citations", []) or []), answer_text=raw_answer
        )
        answer_content = project_answer_content(raw_answer, citations)
        evidence = _normalize_evidence_refs(result)
        search_queries = _normalize_search_queries(
            list(getattr(result, "search_queries", []) or [])
        )
        raw_retrieval_events = list(getattr(result, "retrieval_events", []) or [])
        retrieval_events = (
            normalize_retrieval_events(raw_retrieval_events)
            if raw_retrieval_events
            else legacy_reference_event(citations, search_queries=search_queries)
        )
    except ValueError as error:
        raise ApplicationError(
            f"collection result failed structural validation: {error}",
            type="collection_result_invalid",
            non_retryable=True,
        ) from error
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        # Serialize result accounting for the run. Business-key uniqueness protects
        # duplicate retries of one task, while this lock also prevents distinct
        # tasks completing concurrently from losing a completed_tasks increment.
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.pub_id == run_pub_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        prior = session.scalar(
            select(CollectionTask).where(
                CollectionTask.run_id == run.id,
                CollectionTask.business_key == result.business_key,
            )
        )
        matrix_json = json.dumps(
            _task_matrix(task_input, getattr(result, "browser_instance", None)),
            sort_keys=True,
            separators=(",", ":"),
        )
        citations_json = json.dumps(
            citations, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        evidence_json = json.dumps(
            [asdict(item) for item in evidence],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        response_ast_json = json.dumps(
            answer_content.response_ast,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        search_queries_json = json.dumps(
            search_queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        created = prior is None or prior.state == "pending"
        if prior is None:
            task = CollectionTask(
                # The collection task is also the durable analytics answer identity.
                # Use the answer prefix so API/client projection boundaries agree.
                pub_id=new_pub_id("ans"),
                tenant_id=run.tenant_id,
                run_id=run.id,
                business_key=result.business_key,
                matrix_json=matrix_json,
                state="completed",
                attempt_count=1,
                terminal_at=datetime.now(UTC),
                answer_text=answer_content.response_raw,
                response_markdown_normalized=answer_content.response_markdown_normalized,
                response_ast_json=response_ast_json,
                response_html_sanitized=answer_content.response_html_sanitized,
                response_plain_text=answer_content.response_plain_text,
                response_hash=answer_content.response_hash,
                render_parser_version=answer_content.render_parser_version,
                screenshot_ref=result.screenshot_ref,
                quality_state=result.quality_state,
                citations_json=citations_json,
                evidence_json=evidence_json,
                search_queries_json=search_queries_json,
            )
            session.add(task)
            run.completed_tasks += 1
        elif prior.state == "pending":
            task = prior
            task.state = "completed"
            task.attempt_count = 1
            task.terminal_at = datetime.now(UTC)
            task.answer_text = answer_content.response_raw
            task.response_markdown_normalized = answer_content.response_markdown_normalized
            task.response_ast_json = response_ast_json
            task.response_html_sanitized = answer_content.response_html_sanitized
            task.response_plain_text = answer_content.response_plain_text
            task.response_hash = answer_content.response_hash
            task.render_parser_version = answer_content.render_parser_version
            task.screenshot_ref = result.screenshot_ref
            task.quality_state = result.quality_state
            task.matrix_json = matrix_json
            task.citations_json = citations_json
            task.evidence_json = evidence_json
            task.search_queries_json = search_queries_json
            run.completed_tasks += 1
        else:
            task = prior
            if task.terminal_at is None:
                task.terminal_at = task.updated_at
            if (
                prior.state,
                prior.answer_text,
                prior.response_markdown_normalized,
                prior.response_ast_json,
                prior.response_html_sanitized,
                prior.response_plain_text,
                prior.response_hash,
                prior.render_parser_version,
                prior.screenshot_ref,
                prior.quality_state,
                prior.matrix_json,
                prior.citations_json,
                prior.evidence_json,
                prior.search_queries_json,
            ) != (
                "completed",
                answer_content.response_raw,
                answer_content.response_markdown_normalized,
                response_ast_json,
                answer_content.response_html_sanitized,
                answer_content.response_plain_text,
                answer_content.response_hash,
                answer_content.render_parser_version,
                result.screenshot_ref,
                result.quality_state,
                matrix_json,
                citations_json,
                evidence_json,
                search_queries_json,
            ):
                raise ApplicationError(
                    "collection result replay payload drifted",
                    type="collection_result_payload_drift",
                    non_retryable=True,
                )
        project = session.get(Project, run.project_id)
        if project is None:
            raise ApplicationError("collection project not found", type="project_not_found")
        record_query_attempt(
            session,
            run=run,
            task=task,
            outcome="succeeded",
            error_code=None,
            execution_status="ok",
        )
        mark_source_retry_outcome(
            session,
            run=run,
            business_key=task.business_key,
            succeeded=True,
        )
        evidence_ids_by_relation = _persist_evidence_assets(
            session=session,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project.pub_id,
            run_pub_id=run_pub_id,
            answer_pub_id=task.pub_id,
            business_key=result.business_key,
            adapter_version=task_input.adapter if task_input is not None else "fixed",
            evidence=evidence,
        )
        _persist_answer_share_artifact(
            session=session,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project.pub_id,
            answer_pub_id=task.pub_id,
            platform=task_input.adapter if task_input is not None else "fixed",
            evidence=evidence,
            evidence_ids_by_relation=evidence_ids_by_relation,
        )
        # This flush makes the immutable task identity/timestamp available, but
        # does not commit.  The capture row, evidence links, versioned analysis
        # job and workflow-start command therefore become durable together.
        session.flush()
        _persist_uvw_facts(
            session=session,
            run=run,
            project=project,
            task=task,
            retrieval_events=retrieval_events,
            evidence_ids_by_relation=evidence_ids_by_relation,
            allow_legacy_identity=not raw_retrieval_events,
        )
        _enqueue_answer_analysis(
            session=session,
            tenant_pub_id=tenant_pub_id,
            run=run,
            project=project,
            task=task,
            task_input=_persisted_task_input(task, task_input),
        )
        _persist_answer_capture_event(
            session=session,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project.pub_id,
            run=run,
            task=task,
        )
        run.state = _derive_run_state(run)
        if run.state in {"completed", "completed_with_failures"}:
            # The final per-task transaction is the only gap-free place to
            # hand off run-level work. If the Temporal collection workflow is
            # terminated immediately after this commit, source/risk analysis
            # is still durably queued and can proceed independently.
            _enqueue_post_collection_analysis(
                session=session,
                tenant_pub_id=tenant_pub_id,
                run=run,
                project=project,
            )
        session.commit()
        task_pub_id = task.pub_id
    if created:
        # 采集账号治理旁路上报（2026-08-14 起）：逐题终态记用量台账。
        # 独立事务 + 全异常吞 warning，绝不阻断采集落库主链；幂等重放
        # （任务行已存在）不重报。
        _report_outcome_to_governor(
            run_pub_id=run_pub_id,
            result=result,
            task_input=task_input,
            task_pub_id=task_pub_id,
            status="ok",
        )


def _derive_run_state(run: CollectionRun) -> str:
    """run 进度态推导：全部完成→completed；有失败且全部落定→completed_with_failures；
    否则 running。无失败题时与旧行为（completed/running 二分）完全等价。"""
    if run.completed_tasks >= run.total_tasks:
        return "completed"
    if run.completed_tasks + run.failed_tasks >= run.total_tasks:
        return "completed_with_failures"
    return "running"


def _persist_collection_failure(
    tenant_pub_id: str,
    run_pub_id: str,
    result: CollectionBatchItemResult,
    task_input: CollectionTaskInput | None,
    status: str,
) -> None:
    """失败题（wall/incomplete/aborted）落库：collection_task state="failed"。

    列映射（不新建迁移，全部复用既有列）：

    - ``quality_state`` ← error_type（≤40 字符既有列，机器可读失败类型）；
    - ``evidence_json`` ← 单元素 failure_record JSON（status/error_type/message）。
      该列无证据资产以外的 Python 消费端；此处存的是失败记录而非证据资产，
      以 ``kind="failure_record"`` 注明区分。内容全部确定（不含时间戳），
      保证 activity 重试的 replay drift 检查幂等；
    - ``screenshot_ref`` ← 失败存证截图 ref（墙截图，可选）；
    - ``answer_text`` 保持 None——失败题绝不进答案/分析链路（INV-32 零合成）。

    原始流量证据（2026-08-10 起）：adapter 题末 finally 导出的 raw/HAR ref 随
    ``result.evidence`` 携带，此处与 ok 题同一边界进 CAS（幂等：pub_id 派生 +
    ON CONFLICT DO NOTHING + 资产行 drift 校验）。``evidence_json`` 列保持
    failure_record 原样（replay drift 比较不含证据列表）；墙截图维持现状不进
    CAS（不过 ``_normalize_evidence_refs`` 的截图前置，只规范显式携带的 ref）。
    """
    error_type = (result.error_type or "unknown_failure")[:40]
    error_message = (result.error_message or "")[:1_000]
    # error_message 可能嵌入页面文本——属原始采集材料，原文存储（零 DLP）；
    # error_type/截图 ref 是平台自产词表与路径，保持 fail-closed 自检。
    try:
        _assert_system_ref_secret_free(error_type)
        if result.screenshot_ref:
            _assert_system_ref_secret_free(result.screenshot_ref)
    except ValueError as error:
        raise ApplicationError(
            "collection result rejected by DLP",
            type="collection_result_dlp_rejected",
            non_retryable=True,
        ) from error
    try:
        evidence = _normalize_evidence_list(list(result.evidence or []))
    except ValueError as error:
        raise ApplicationError(
            f"collection result failed structural validation: {error}",
            type="collection_result_invalid",
            non_retryable=True,
        ) from error
    failure_json = json.dumps(
        [
            {
                "kind": "failure_record",
                "status": status,
                "error_type": error_type,
                "message": error_message,
            }
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        # 与 ok 路径同款 run 行锁：串行化 run 计数（failed_tasks 增量不丢）。
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.pub_id == run_pub_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        prior = session.scalar(
            select(CollectionTask).where(
                CollectionTask.run_id == run.id,
                CollectionTask.business_key == result.business_key,
            )
        )
        matrix_json = json.dumps(
            _task_matrix(task_input, result.browser_instance),
            sort_keys=True,
            separators=(",", ":"),
        )
        created = prior is None or prior.state == "pending"
        if prior is None:
            task = CollectionTask(
                pub_id=new_pub_id("ans"),
                tenant_id=run.tenant_id,
                run_id=run.id,
                business_key=result.business_key,
                matrix_json=matrix_json,
                state="failed",
                attempt_count=1,
                terminal_at=datetime.now(UTC),
                answer_text=None,
                screenshot_ref=result.screenshot_ref,
                quality_state=error_type,
                citations_json="[]",
                evidence_json=failure_json,
                search_queries_json="[]",
            )
            session.add(task)
            run.failed_tasks += 1
        elif prior.state == "pending":
            task = prior
            task.state = "failed"
            task.attempt_count = 1
            task.terminal_at = datetime.now(UTC)
            task.answer_text = None
            task.screenshot_ref = result.screenshot_ref
            task.quality_state = error_type
            task.matrix_json = matrix_json
            task.citations_json = "[]"
            task.evidence_json = failure_json
            task.search_queries_json = "[]"
            run.failed_tasks += 1
        else:
            task = prior
            if task.terminal_at is None:
                task.terminal_at = task.updated_at
            if (
                prior.state,
                prior.quality_state,
                prior.matrix_json,
                prior.evidence_json,
                prior.screenshot_ref,
            ) != (
                "failed",
                error_type,
                matrix_json,
                failure_json,
                result.screenshot_ref,
            ):
                raise ApplicationError(
                    "collection result replay payload drifted",
                    type="collection_result_payload_drift",
                    non_retryable=True,
                )
        project = session.get(Project, run.project_id)
        if project is None:
            raise ApplicationError("collection project not found", type="project_not_found")
        record_query_attempt(
            session,
            run=run,
            task=task,
            outcome="failed",
            error_code=error_type,
            execution_status=status,
        )
        record_query_failure_knowledge(
            session,
            run=run,
            task=task,
            error_code=error_type,
            execution_status=status,
        )
        mark_source_retry_outcome(
            session,
            run=run,
            business_key=task.business_key,
            succeeded=False,
            error_code=error_type,
        )
        _persist_evidence_assets(
            session=session,
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project.pub_id,
            run_pub_id=run_pub_id,
            answer_pub_id=task.pub_id,
            business_key=result.business_key,
            adapter_version=task_input.adapter if task_input is not None else "fixed",
            evidence=evidence,
        )
        run.state = _derive_run_state(run)
        if run.state in {"completed", "completed_with_failures"}:
            _enqueue_post_collection_analysis(
                session=session,
                tenant_pub_id=tenant_pub_id,
                run=run,
                project=project,
            )
        session.commit()
        task_pub_id = task.pub_id
    if created:
        # 采集账号治理旁路上报（2026-08-14 起）：逐题终态（失败收敛熔断）+
        # 墙 outcome 的 report_wall（quota/muted/captcha 改写账号状态）。
        # 独立事务 + 全异常吞 warning，绝不阻断采集落库主链；幂等重放不重报。
        _report_outcome_to_governor(
            run_pub_id=run_pub_id,
            result=result,
            task_input=task_input,
            task_pub_id=task_pub_id,
            status=status,
        )


@activity.defn
def finalize_account_revocation(tenant_pub_id: str, account_pub_id: str) -> RevocationResult:
    """Idempotently propagates revocation through leases and encrypted profile versions."""
    from datetime import UTC, datetime

    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        account = session.scalar(
            select(PlatformAccount).where(PlatformAccount.pub_id == account_pub_id)
        )
        if account is None:
            raise ApplicationError(
                "platform account does not exist",
                type="account_not_found",
                non_retryable=True,
            )
        request = session.scalar(
            select(RevocationRequest)
            .where(RevocationRequest.account_id == account.id)
            .order_by(RevocationRequest.created_at.desc())
            .with_for_update()
        )
        if request is None:
            # The API durably starts this workflow immediately before committing
            # the staged request. A fast Activity may observe the pre-commit
            # snapshot once; typed retry bridges that intentional handoff.
            raise ApplicationError(
                "revocation request is not committed yet",
                type="revocation_request_not_committed",
            )
        now = datetime.now(UTC)
        leases = session.scalars(
            select(SessionLease).where(
                SessionLease.account_id == account.id,
                SessionLease.released_at.is_(None),
            )
        ).all()
        for lease in leases:
            lease.released_at = now
        profiles = session.scalars(
            select(BrowserProfile).where(BrowserProfile.account_id == account.id)
        ).all()
        # Delete first. If the database commit later fails, Temporal retries;
        # an already-missing Vault key is accepted idempotently.
        _destroy_production_account_key(tenant_pub_id, account.pub_id, len(profiles))
        for profile in profiles:
            profile.state = "PURGED"
            profile.ciphertext = None
            profile.nonce = None
            profile.wrapped_dek = None
            profile.purged_at = profile.purged_at or now
        capability_leases = session.scalars(
            select(CapabilityLease).where(
                CapabilityLease.account_id == account.id,
                CapabilityLease.revoked_at.is_(None),
            )
        ).all()
        for capability_lease in capability_leases:
            capability_lease.revoked_at = now
        device_bindings = session.scalars(
            select(DeviceBinding).where(DeviceBinding.account_id == account.id)
        ).all()
        for device in device_bindings:
            device.state = "revoked"
            device.revoked_at = device.revoked_at or now
        interventions = session.scalars(
            select(InterventionRequest).where(InterventionRequest.account_id == account.id)
        ).all()
        intervention_ids = [item.id for item in interventions]
        terminal_tasks = (
            session.scalars(
                select(TerminalTask).where(TerminalTask.intervention_id.in_(intervention_ids))
            ).all()
            if intervention_ids
            else []
        )
        for terminal_task in terminal_tasks:
            if terminal_task.state == "issued":
                terminal_task.state = "revoked"
        for intervention in interventions:
            if intervention.state in {"pending", "paired", "task_issued"}:
                intervention.state = "revoked"
            intervention.pairing_token_hash = None
        account.state = "revoked"
        request.state = "completed"
        request.deletion_verified_at = now
        prior_event = session.scalar(
            select(SessionEvent).where(
                SessionEvent.account_id == account.id,
                SessionEvent.event_type == "account.revocation.completed",
            )
        )
        if prior_event is None:
            session.add(
                SessionEvent(
                    pub_id=new_pub_id("sev"),
                    tenant_id=account.tenant_id,
                    account_id=account.id,
                    event_type="account.revocation.completed",
                    summary_json=json.dumps({"request_pub_id": request.pub_id}),
                )
            )
        session.commit()
        return RevocationResult(
            account_pub_id=account.pub_id,
            released_leases=len(leases),
            purged_profile_versions=[item.profile_version for item in profiles],
            revoked_device_bindings=len(device_bindings),
            revoked_terminal_tasks=len(terminal_tasks),
            revoked_interventions=len(interventions),
            revoked_capability_leases=len(capability_leases),
            deletion_verified=True,
        )


@activity.defn
def prepare_collection_session(
    tenant_pub_id: str, account_pub_id: str, holder: str, required_scope: str
) -> SessionPreparation:
    from datetime import UTC, datetime, timedelta

    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        account = session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.pub_id == account_pub_id,
                PlatformAccount.state.in_(["active", "challenge_required"]),
            )
        )
        if account is None:
            raise ApplicationError(
                "platform account is not active",
                type="account_not_active",
                non_retryable=True,
            )
        authorization = session.scalar(
            select(AccountAuthorization)
            .where(
                AccountAuthorization.account_id == account.id,
                AccountAuthorization.revoked_at.is_(None),
                AccountAuthorization.valid_from <= datetime.now(UTC),
                AccountAuthorization.valid_until > datetime.now(UTC),
            )
            .order_by(AccountAuthorization.created_at.desc())
        )
        if authorization is None or required_scope not in json.loads(authorization.scopes_json):
            raise ApplicationError(
                "requested scope is not authorized",
                type="scope_not_authorized",
                non_retryable=True,
            )
        profile = session.scalar(
            select(BrowserProfile)
            .where(
                BrowserProfile.account_id == account.id,
                BrowserProfile.state == "ACTIVE",
            )
            .order_by(BrowserProfile.profile_version.desc())
        )
        if profile is None:
            raise ApplicationError(
                "active profile was not found",
                type="active_profile_not_found",
                non_retryable=True,
            )
        lease = acquire_session_lease(
            session,
            account,
            profile,
            holder,
            required_scope,
            timedelta(minutes=20),
        )
        session.commit()
        return SessionPreparation(
            lease_pub_id=lease.pub_id,
            fencing_token=lease.fencing_token,
            profile_version=profile.profile_version,
        )


@activity.defn
def release_collection_session(tenant_pub_id: str, lease_pub_id: str, fencing_token: int) -> None:
    from datetime import UTC, datetime

    with WorkerSessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        lease = session.scalar(
            select(SessionLease).where(SessionLease.pub_id == lease_pub_id).with_for_update()
        )
        if lease is None:
            return
        if lease.fencing_token != fencing_token:
            raise ApplicationError(
                "session lease fencing token does not match",
                type="fence_violation",
                non_retryable=True,
            )
        lease.released_at = lease.released_at or datetime.now(UTC)
        session.commit()
