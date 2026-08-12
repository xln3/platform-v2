import json
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
from geo_platform.collection.vault import KmsUnavailableError, VaultTransitKms
from geo_platform.config import get_settings
from geo_platform.evidence.object_store import ContentAddressedObjectStore
from geo_platform.projects.models import Brand, Competitor, MonitoringConfigVersion, Project
from geo_platform.tenancy.database import WorkerSessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.repository import TenantRepository
from sqlalchemy import select, text
from temporalio import activity
from temporalio.exceptions import ApplicationError

from domain.evidence.dlp import assert_secret_free


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
PLATFORM_MODE_CAPABILITIES: dict[str, frozenset[str]] = {
    "doubao": frozenset({"normal", "deep_think"}),
    "deepseek": frozenset({"normal", "deep_think"}),
    "tongyi": frozenset({"normal", "deep_think"}),
    "yiyan": frozenset({"normal", "deep_think"}),
    "yuanbao": frozenset({"normal", "deep_think"}),
}


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
    "share_image",
    "share_link",
    "source_screenshot",
    "sse",
    # 原始流量留痕（2026-08-10 起，用户拍板默认开）：sse_raw=completion 端点
    # 原始响应体；har=本题页面级 HAR 1.2。绝不可复用 kind="sse"——trace 端点
    # 硬过滤 kind='sse' AND relation='answer_sse_trace'，复用会污染读面。
    "sse_raw",
    "har",
}
_EVIDENCE_RELATIONS = {
    "answer_page",
    "official_share_image",
    "official_share_link",
    "cited_source_snapshot",
    "ai_opened_source_preview",
    "answer_sse_trace",
    "answer_sse_raw",
    "answer_har",
}
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_MAX_EVIDENCE_BYTES = 30 * 1024 * 1024
_MAX_SEARCH_QUERIES = 200


def _normalize_search_queries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """平台真实检索词（W1）规范化：[{"query": str, "ordinal": int}]，上限 200 条。

    原始采集原则：平台输出是测量原料，**原文存储、不做任何脱敏**
    （2026-08-06 用户拍板；DLP 只管会话侧秘密/intake 边界，不碰公开内容）。
    """
    if len(items) > _MAX_SEARCH_QUERIES:
        raise ValueError("collection result has too many search queries")
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


def _normalize_citations(items: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    """引用规范化：结构校验（URL/长度/去重）；文本为公开内容，原文存储不脱敏。"""
    if len(items) > 500:  # deep_think 检索流实测引用卡片可破百（20260810）
        raise ValueError("collection result has too many citations")
    normalized: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("collection citation must be an object")
        url = _safe_http_url(item.get("url"))
        assert url is not None
        if url in seen:
            continue
        seen.add(url)
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
        normalized.append({"url": url, "title": title, "cited_text": cited_text})
    return normalized


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
    assert_secret_free(str(path))
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
    return _normalize_evidence_list(raw_items)


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


def _normalize_evidence_list(raw_items: list[Any]) -> list[CollectionEvidenceRef]:
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
            )
        )
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
) -> None:
    if not evidence:
        return
    settings = get_settings()
    store = ContentAddressedObjectStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
    )
    store.ensure_bucket()
    capture_time = datetime.now(UTC)
    for index, item in enumerate(evidence, 1):
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
        stored = store.put_redacted(Path(item.path).read_bytes(), mime_type=item.mime_type)
        session.execute(
            text(
                """
                INSERT INTO evidence.evidence_asset
                  (pub_id,tenant_pub_id,project_pub_id,kind,access_class,sha256,object_key,
                   mime_type,byte_size,source_url,dlp_findings,channel,authorization_scope,
                   adapter_version,capture_time,authorized_session_capture)
                VALUES
                  (:pub_id,:tenant_pub_id,:project_pub_id,:kind,'customer_private',:sha256,
                   :object_key,:mime_type,:byte_size,:source_url,:dlp_findings,'web',
                   CAST(:authorization_scope AS text[]),:adapter_version,:capture_time,false)
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
            },
        )
        persisted = (
            session.execute(
                text(
                    """
                SELECT tenant_pub_id,project_pub_id,kind,sha256,object_key,mime_type,byte_size,
                       source_url,adapter_version
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
        }
        if dict(persisted) != expected:
            raise ApplicationError(
                "evidence replay payload drifted",
                type="collection_evidence_payload_drift",
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


@activity.defn
def publish_downstream_event(
    run_pub_id: str,
    tenant_pub_id: str | None = None,
    task_inputs: list[CollectionTaskInput] | None = None,
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
        analysis_commands = 0
        analysis_expected = 0
        analysis_admission = "not_requested"
        if task_inputs is not None:
            project = session.get(Project, run.project_id)
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
            task_by_key = {item.business_key: item for item in task_inputs}
            completed = list(
                session.scalars(
                    select(CollectionTask)
                    .where(CollectionTask.run_id == run.id, CollectionTask.state == "completed")
                    .order_by(CollectionTask.created_at, CollectionTask.pub_id)
                )
            )
            if brand is None:
                analysis_admission = "missing_brand"
            elif project is not None:
                analysis_expected = len(completed)
                for task in completed:
                    task_input = task_by_key.get(task.business_key)
                    if task_input is None or task.answer_text is None:
                        continue
                    analysis_workflow_id = (
                        f"answer-analysis/{tenant_pub_id}/{run_pub_id}/{task.pub_id}"
                    )
                    analysis_payload = {
                        "persist": True,
                        "tenant_pub_id": tenant_pub_id,
                        "project_pub_id": project.pub_id,
                        "answer_pub_id": task.pub_id,
                        "text": task.answer_text,
                        "brand": brand.name,
                        "competitors": [item.name for item in competitors],
                        "citations": json.loads(task.citations_json or "[]"),
                        "search_queries": json.loads(task.search_queries_json or "[]"),
                        "dimensions": _analysis_dimensions(
                            task_input,
                            run_pub_id=run_pub_id,
                            config_version_pub_id=(
                                config_version.pub_id if config_version is not None else None
                            ),
                            # 实例键真源 = 落库 matrix_json（batch 采集时的实测
                            # 常驻实例）；无记录（历史/per-task 老路径）→ None
                            # → provenance 回退 slug 查表（旧行为）。
                            browser_instance=(
                                json.loads(task.matrix_json or "{}").get("browser_instance") or None
                            ),
                        ),
                        "own_domains": [brand.website] if brand.website else [],
                        "adapter_version": task_input.adapter,
                        "capture_time": task.created_at.astimezone(UTC).isoformat(),
                        "channel": "api",
                        "access_class": "customer_private",
                        "scorer_version": "scorer-v2",
                        "metric_version": "metrics-v2",
                        "model_version": "rules-v1",
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
                            "workflow_id": analysis_workflow_id,
                            "task_queue": get_settings().s02_temporal_task_queue,
                            "payload": json.dumps(
                                analysis_payload, sort_keys=True, separators=(",", ":")
                            ),
                        },
                    ).scalar_one()
                    if persisted_payload != analysis_payload:
                        raise ApplicationError(
                            "answer analysis workflow replay payload drifted",
                            type="analysis_workflow_payload_drift",
                            non_retryable=True,
                        )
                    analysis_commands += 1
                if analysis_expected == 0:
                    analysis_admission = "missing_completed_answers"
                elif analysis_commands == analysis_expected:
                    analysis_admission = "enqueued"
                else:
                    analysis_admission = "partial_fanout"
        session.execute(
            text(
                """
                UPDATE integration.outbox_event
                SET payload=payload || CAST(:admission AS jsonb),
                    published_at=CASE WHEN :acknowledged THEN COALESCE(published_at,now())
                                      ELSE published_at END,
                    attempts=attempts+CASE WHEN :acknowledged THEN 1 ELSE 0 END
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
                    },
                    separators=(",", ":"),
                ),
                "acknowledged": analysis_admission == "enqueued",
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
        # Completion written by persist_collection_result is already terminal;
        # retries must not demote it. completed_with_failures 同属 s04_0019 终态
        # 词表（触发器 ck_collection_run_terminal_state 冻结），同样不得改写。
        # 与触发器 ck_collection_run_terminal_state 词表严格对齐（含 skipped）：
        # 词表不一致 = reconcile/收尾 UPDATE 反复撞 23514 毒循环（20260806 生产实证）。
        terminal_states = {"completed", "completed_with_failures", "cancelled", "failed", "skipped"}
        if run.state not in terminal_states:
            run.state = state
            run.error_code = error_code
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
            assert_secret_free(result.screenshot_ref)
    except ValueError as error:
        raise ApplicationError(
            "collection result rejected by DLP",
            type="collection_result_dlp_rejected",
            non_retryable=True,
        ) from error
    try:
        citations = _normalize_citations(list(getattr(result, "citations", []) or []))
        evidence = _normalize_evidence_refs(result)
        search_queries = _normalize_search_queries(
            list(getattr(result, "search_queries", []) or [])
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
        search_queries_json = json.dumps(
            search_queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
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
                answer_text=result.answer_text,
                screenshot_ref=result.screenshot_ref,
                quality_state=result.quality_state,
                citations_json=citations_json,
                evidence_json=evidence_json,
                search_queries_json=search_queries_json,
            )
            session.add(task)
            run.completed_tasks += 1
        else:
            task = prior
            if (
                prior.answer_text,
                prior.screenshot_ref,
                prior.quality_state,
                prior.matrix_json,
                prior.citations_json,
                prior.evidence_json,
                prior.search_queries_json,
            ) != (
                result.answer_text,
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
        session.commit()


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
        assert_secret_free(error_type)
        if result.screenshot_ref:
            assert_secret_free(result.screenshot_ref)
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
        if prior is None:
            task = CollectionTask(
                pub_id=new_pub_id("ans"),
                tenant_id=run.tenant_id,
                run_id=run.id,
                business_key=result.business_key,
                matrix_json=matrix_json,
                state="failed",
                attempt_count=1,
                answer_text=None,
                screenshot_ref=result.screenshot_ref,
                quality_state=error_type,
                citations_json="[]",
                evidence_json=failure_json,
                search_queries_json="[]",
            )
            session.add(task)
            run.failed_tasks += 1
        else:
            task = prior
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
        session.commit()


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
