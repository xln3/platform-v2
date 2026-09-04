"""人工补测登记通道（manual-ingestion-v1）。

背景：平台风控导致个别题采集失败时，运营在浏览器里人工实测拿到的回答，
需要登记为带 provenance 的正式 ``analytics.answer`` 行，而不是散落在
临时脚本产物里。

设计决策（2026-09-01）：

- **同构写入**：复用 ``AnalyticsService.analyze_and_persist``（采集 fanout
  的同一个写函数/同一段 SQL）——answer/answer_analysis/metric_trace/
  metric_daily/outbox 全部走既有路径，manual 行与采集行结构完全一致。
- **run 归属**：``analytics.answer.run_pub_id`` 自 s04_0023 起就是可空列
  （legacy 行同样为 NULL），人工补测没有采集 run，**诚实置 NULL**——不为
  省事伪造 ``platform.collection_run`` 行，也不放松任何约束。channel=
  manual + adapter_version=manual-ingest-v1 是辨识口径。
- **INV-1 合格口径**：dimensions 刻意**不盖**五元 provenance 键
  （captcha_mode/geo_source/account_source/rate_policy/degraded_flag）——
  人工实测的五元语义由登记人本人承担（撞码由人解、地域=登记声明的
  region），盖章成采集侧取值是编造。无五元键走
  ``resolve_measurement_eligibility`` 旧路径：eligible 缺省 true（与
  2026-08-08 前存量行同一继承口径），读路径只排显式不合格，故 manual
  行默认计入测量，provenance 可辨 channel=manual。
- **provenance**：channel=MANUAL；platform_account/browser_profile/
  session_event 三元 None（人工实测无平台账号体系可挂）；operator/reason/
  原始 capture_time（人工实测时间，不是登记时间）落在 dimensions
  （manual_operator/manual_reason[/manual_source_url]），随
  answer_analysis.feature_payload 与 metric 快照持久可查。
- **幂等**：answer pub_id 确定性派生=
  sha256(tenant|project|显式 idempotency_key 或 model|query_text|
  capture_time)。重复登记命中 ``ON CONFLICT (tenant_pub_id,pub_id)``
  返回既有行（payload 逐字段一致校验，漂移即 409，绝不静默覆盖）。
- **证据附件**：只关联**既有** evidence 资产（截图已入 CAS 的 evd_ pub_id）
  ——登记前校验资产存在且属本租户，登记后插 ``evidence.evidence_relation``
  （relation_type=manual_capture_attachment，幂等 ON CONFLICT DO
  NOTHING），``GET /answers/{id}/relations`` 原样可见。新截图上传走
  EvidenceService 另行处理，不在本通道 scope。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from psycopg.rows import dict_row

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.eligibility import resolve_measurement_eligibility

from ..tenancy.psycopg import tenant_connection
from .service import AnalyticsService

MANUAL_ADAPTER_VERSION = "manual-ingest-v1"
MANUAL_EVIDENCE_RELATION_TYPE = "manual_capture_attachment"

# 与采集 fanout（workflows/activities/collection.py analysis_contract）同一
# 分析版本三元——manual 行与采集行走同一评分/指标口径，不发明第二套版本。
_SCORER_VERSION = "scorer-v2"
_METRIC_VERSION = "metrics-v2"
_MODEL_VERSION = "rules-v1"


@dataclass(frozen=True, slots=True)
class ManualAnswerItem:
    """一条人工实测回答的登记输入。``model`` = 平台 slug（与采集行 answer.model
    同词表：doubao/deepseek/tongyi/yiyan/yuanbao）；``capture_time`` = 人工
    实测时间（必须 tz-aware），不是登记时间。"""

    model: str
    query_text: str
    response_plain_text: str
    capture_time: datetime
    region: str = "unknown"
    mode: str = "normal"
    source_url: str | None = None
    evidence_pub_ids: tuple[str, ...] = ()
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ManualAnswerRegistration:
    answer_pub_id: str
    analysis_pub_id: str
    created: bool
    eligible: bool
    evidence_attached: int


class ProjectNotFound(LookupError):
    """project 在本租户内不存在 → API 404 project_not_found（跨租户同 404）。"""


class BrandMissing(ValueError):
    """项目无 brand 登记 → 无法做提及分析，fail-loud 422（不注册半成品行）。"""


class UnknownEvidencePubId(ValueError):
    """证据资产不存在/不属本租户/已删除 → 422 unknown_evidence_pub_id。"""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"unknown evidence pub_ids: {','.join(missing)}")
        self.missing = missing


class RegistrationConflict(ValueError):
    """同一幂等键重复登记但 payload 漂移（同键不同文）→ 409，绝不静默覆盖。"""

    def __init__(self, answer_pub_id: str) -> None:
        super().__init__(f"manual answer payload drifted: {answer_pub_id}")
        self.answer_pub_id = answer_pub_id


def manual_answer_pub_id(*, tenant_pub_id: str, project_pub_id: str, item: ManualAnswerItem) -> str:
    """确定性 answer pub_id：显式 idempotency_key 优先，否则
    model|query_text|capture_time 组合。同输入重登记 → 同 pub_id → 命中
    ON CONFLICT 返回既有行。"""
    basis = item.idempotency_key or "|".join(
        (item.model, item.query_text, item.capture_time.astimezone(UTC).isoformat())
    )
    digest = sha256(f"manual-answer|{tenant_pub_id}|{project_pub_id}|{basis}".encode()).hexdigest()
    return f"ans_{digest[:26]}"


def _manual_dimensions(*, operator: str, reason: str, item: ManualAnswerItem) -> dict[str, str]:
    """manual 行的 dimensions：不含 INV-1 五元键（走旧路径 eligible 缺省 true），
    不含 run_pub_id/config_version_pub_id（answer 列置 NULL）。登记人/原因/
    来源链接随 dimensions 持久化（feature_payload + metric 快照）。"""
    dimensions = {
        "query_text": item.query_text,
        "model": item.model,
        "region": item.region,
        "mode": item.mode,
        "channel": CaptureChannel.MANUAL.value,
        "manual_operator": operator,
        "manual_reason": reason,
    }
    if item.source_url:
        dimensions["manual_source_url"] = item.source_url
    return dimensions


def _link_evidence(
    dsn: str, tenant_pub_id: str, *, answer_pub_id: str, evidence_pub_ids: tuple[str, ...]
) -> int:
    """把既有证据资产关联到 manual answer（幂等；relations 读端点原样可见）。"""
    if not evidence_pub_ids:
        return 0
    with tenant_connection(dsn, tenant_pub_id) as connection:
        for evidence_pub_id in evidence_pub_ids:
            connection.execute(
                """
                INSERT INTO evidence.evidence_relation
                  (tenant_pub_id,from_pub_id,to_pub_id,relation_type)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation_type) DO NOTHING
                """,
                (tenant_pub_id, answer_pub_id, evidence_pub_id, MANUAL_EVIDENCE_RELATION_TYPE),
            )
    return len(evidence_pub_ids)


def register_manual_answers(
    dsn: str,
    *,
    tenant_pub_id: str,
    project_pub_id: str,
    operator: str,
    reason: str,
    items: tuple[ManualAnswerItem, ...],
) -> list[ManualAnswerRegistration]:
    """批量登记人工实测回答。任一 item 失败整批 fail-loud（确定性 pub_id
    保证修正后重试安全：已登记项返回既有行，不产生重复）。"""
    if not operator.strip():
        raise ValueError("operator must be non-empty")
    if not reason.strip():
        raise ValueError("reason must be non-empty")
    if not items:
        raise ValueError("items must be non-empty")
    for item in items:
        if item.capture_time.tzinfo is None:
            raise ValueError("capture_time must be timezone-aware")
        if not item.query_text.strip() or not item.response_plain_text.strip():
            raise ValueError("query_text and response_plain_text must be non-empty")

    evidence_ids = sorted({pub_id for item in items for pub_id in item.evidence_pub_ids})
    wanted = [
        (
            item,
            manual_answer_pub_id(
                tenant_pub_id=tenant_pub_id, project_pub_id=project_pub_id, item=item
            ),
        )
        for item in items
    ]
    with tenant_connection(dsn, tenant_pub_id, row_factory=dict_row) as connection:
        project = connection.execute(
            "SELECT id FROM platform.project WHERE pub_id=%s", (project_pub_id,)
        ).fetchone()
        if project is None:
            raise ProjectNotFound("project_not_found")
        brand = connection.execute(
            """
            SELECT name,website FROM platform.brand
            WHERE project_id=%s ORDER BY created_at,pub_id LIMIT 1
            """,
            (project["id"],),
        ).fetchone()
        if brand is None:
            raise BrandMissing("manual_answer_brand_missing")
        competitors = tuple(
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM platform.competitor WHERE project_id=%s"
                " ORDER BY created_at,pub_id",
                (project["id"],),
            ).fetchall()
        )
        if evidence_ids:
            found = {
                str(row["pub_id"])
                for row in connection.execute(
                    """
                    SELECT pub_id FROM evidence.evidence_asset
                    WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[]) AND deleted_at IS NULL
                    """,
                    (tenant_pub_id, evidence_ids),
                ).fetchall()
            }
            missing = [pub_id for pub_id in evidence_ids if pub_id not in found]
            if missing:
                raise UnknownEvidencePubId(missing)
        existing = {
            str(row["pub_id"])
            for row in connection.execute(
                "SELECT pub_id FROM analytics.answer"
                " WHERE tenant_pub_id=%s AND pub_id=ANY(%s::text[])",
                (tenant_pub_id, [answer_pub_id for _, answer_pub_id in wanted]),
            ).fetchall()
        }

    service = AnalyticsService(dsn=dsn)
    registrations: list[ManualAnswerRegistration] = []
    for item, answer_pub_id in wanted:
        dimensions = _manual_dimensions(operator=operator, reason=reason, item=item)
        eligible, _degraded, _stamped = resolve_measurement_eligibility(dimensions)
        provenance = RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.MANUAL,
            authorization_scope=(),
            adapter_version=MANUAL_ADAPTER_VERSION,
            capture_time=item.capture_time,
            access_class=AccessClass.CUSTOMER_PRIVATE,
        )
        try:
            persisted = service.analyze_and_persist(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=project_pub_id,
                answer_pub_id=answer_pub_id,
                answer_text=item.response_plain_text,
                brand=str(brand["name"]),
                competitors=competitors,
                citations=(),
                dimensions=dimensions,
                own_domains=(str(brand["website"]),) if brand["website"] else (),
                provenance=provenance,
                scorer_version=_SCORER_VERSION,
                metric_version=_METRIC_VERSION,
                model_version=_MODEL_VERSION,
            )
        except ValueError as exc:
            if "drifted" in str(exc):
                raise RegistrationConflict(answer_pub_id) from exc
            raise
        attached = _link_evidence(
            dsn, tenant_pub_id, answer_pub_id=answer_pub_id, evidence_pub_ids=item.evidence_pub_ids
        )
        registrations.append(
            ManualAnswerRegistration(
                answer_pub_id=answer_pub_id,
                analysis_pub_id=str(persisted["analysis_pub_id"]),
                created=answer_pub_id not in existing,
                eligible=eligible,
                evidence_attached=attached,
            )
        )
    return registrations
