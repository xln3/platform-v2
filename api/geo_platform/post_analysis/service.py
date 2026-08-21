"""信源帖子取证分析 service：任务/条目读写 + 资产下载（platform RLS 双 selector）。

规格：developlog/specs/post-analysis-20260806.md §4/§6。

纪律：
- pub_id 确定性派生：task=``pat_``+sha256(tenant|idempotency_key 或请求体指纹)，
  item=``pai_``+sha256(tenant|task_pub_id|url_hash)；ON CONFLICT DO NOTHING。
- Idempotency-Key：同 key 同请求体 → 重放原任务；同 key 不同体 → 409 冲突。
  key 缺省时按请求体指纹派生 pub_id（相同提交天然幂等重放）。
- 同事务插 ``integration.workflow_start_command``（workflow_type='post_analysis'），
  走既有 outbox 派发纪律。
- 平台表 RLS 按 app.tenant_id（uuid）：先按 pub_id 解析 tenant 再置双 selector
  （analytics/service.py 同款口径）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..evidence.object_store import ContentAddressedObjectStore

TASK_PUB_PREFIX = "pat"
ITEM_PUB_PREFIX = "pai"
WORKFLOW_TYPE = "post_analysis"

# task.status 词表：queued / running / completed / partial / failed
# item.status 词表：pending / fetching / analyzing / annotating / completed /
# fetch_failed / analysis_failed

_ASSET_KINDS = ("screenshot", "annotated")


class PostAnalysisNotFound(LookupError):
    """请求的任务/条目/资产不在租户范围内。"""


class PostAnalysisConflict(RuntimeError):
    """同 Idempotency-Key 不同请求体 → 409 idempotency_conflict。"""


class PostAnalysisInvalid(ValueError):
    """URL 列表非法（非 http/https、不可归一化、超上限、去重后为空）。"""


@dataclass(frozen=True)
class NormalizedUrl:
    url: str  # 原样（strip 后）提交的 URL
    key: str  # 归一化去重键
    url_hash: str  # sha256(key)
    host: str
    ordinal: int


def _normalize_url_key(url: str) -> tuple[str, str] | None:
    """→ (归一化键, host)；非 http/https 或不可归一化 → None。与 source_fetch 同口径。"""
    from workflows.activities.source_fetch import normalize_host, url_dedupe_key

    candidate = url.strip()
    if not candidate:
        return None
    key = url_dedupe_key(candidate)
    if key is None:
        return None
    host = normalize_host(candidate)
    if host is None:
        return None
    return key, host


def validate_urls(urls: list[str], *, max_urls: int) -> list[NormalizedUrl]:
    """URL 校验：仅 http/https、按归一化键去重（保序）、上限 max_urls。非法即抛。"""
    if not urls:
        raise PostAnalysisInvalid("urls 不能为空")
    invalid = [url for url in urls if _normalize_url_key(url) is None]
    if invalid:
        raise PostAnalysisInvalid(f"非法 URL（仅支持 http/https）：{invalid[:5]}")
    targets: list[NormalizedUrl] = []
    seen: set[str] = set()
    for url in urls:
        normalized = _normalize_url_key(url)
        assert normalized is not None  # 上面已校验
        key, host = normalized
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            NormalizedUrl(
                url=url.strip(),
                key=key,
                url_hash=sha256(key.encode()).hexdigest(),
                host=host,
                ordinal=len(targets),
            )
        )
    if len(targets) > max_urls:
        raise PostAnalysisInvalid(f"去重后 URL 数 {len(targets)} 超上限 {max_urls}")
    return targets


def request_fingerprint(
    *,
    target_brand: str,
    target_brand_aliases: list[str],
    urls: list[NormalizedUrl],
    options: Mapping[str, Any],
) -> str:
    """请求体指纹：幂等重放/冲突比对用（与请求内容一一对应）。"""
    canonical = json.dumps(
        {
            "target_brand": target_brand,
            "target_brand_aliases": target_brand_aliases,
            "urls": [target.key for target in urls],
            "options": dict(options),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def derive_task_pub_id(tenant_pub_id: str, idempotency_key: str | None, fingerprint: str) -> str:
    """task pub_id 确定性派生：key 优先，缺省按请求体指纹。"""
    material = idempotency_key if idempotency_key else f"body:{fingerprint}"
    stable_key = "|".join((tenant_pub_id, material))
    return f"{TASK_PUB_PREFIX}_{sha256(stable_key.encode()).hexdigest()[:26]}"


def derive_item_pub_id(tenant_pub_id: str, task_pub_id: str, url_hash: str) -> str:
    """item pub_id 确定性派生：同 (tenant,task,url_hash) 必同 id。"""
    stable_key = "|".join((tenant_pub_id, task_pub_id, url_hash))
    return f"{ITEM_PUB_PREFIX}_{sha256(stable_key.encode()).hexdigest()[:26]}"


def workflow_id_for(tenant_pub_id: str, task_pub_id: str) -> str:
    """workflow id（CONVENTIONS：{workflow-type}/{tenant}/{aggregate}）。"""
    return f"post-analysis/{tenant_pub_id}/{task_pub_id}"


def _public_task(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "id"}


def _public_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"id", "task_id"}}


def item_list_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """列表行派生字段：类别/GEO 徽章/拉踩与不实计数（从 analysis JSONB 提取）。"""
    analysis = row.get("analysis")
    data = analysis if isinstance(analysis, dict) else {}
    claims = data.get("claims")
    misinformation = 0
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            verification = claim.get("verification")
            if isinstance(verification, dict) and verification.get("verdict") == "inaccurate":
                misinformation += 1
    disparagement = data.get("disparagement")
    return {
        "category": data.get("category"),
        "category_label": data.get("category_label"),
        "is_geo_post": data.get("is_geo_post"),
        "is_target_brand_geo": data.get("is_target_brand_geo"),
        "disparagement_count": len(disparagement) if isinstance(disparagement, list) else 0,
        "misinformation_count": misinformation,
    }


class PostAnalysisService:
    """dsn + 可注入连接工厂（单测 fake 注入）。"""

    def __init__(
        self,
        *,
        dsn: str,
        max_urls_per_task: int = 50,
        connect: Callable[[], psycopg.Connection[Any]] | None = None,
        object_store: ContentAddressedObjectStore | None = None,
    ) -> None:
        self.dsn = dsn
        self.max_urls_per_task = max_urls_per_task
        self._connect_factory = connect
        self._object_store = object_store

    def _new_connection(self) -> psycopg.Connection[Any]:
        if self._connect_factory is not None:
            return self._connect_factory()
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @contextmanager
    def _tenant_conn(self, tenant_pub_id: str) -> Iterator[tuple[psycopg.Connection[Any], str]]:
        """platform schema 连接：解析 tenant uuid + 置 app.tenant_id/app.tenant_pub_id。"""
        with self._new_connection() as connection:
            tenant_row = connection.execute(
                "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
            ).fetchone()
            if tenant_row is None:
                raise PostAnalysisNotFound("tenant not found")
            tenant_id = str(tenant_row["id"])
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true), "
                "set_config('app.tenant_pub_id', %s, true)",
                (tenant_id, tenant_pub_id),
            )
            yield connection, tenant_id

    # -- 创建（幂等 + outbox 同事务） ----------------------------------------

    def create_task(
        self,
        *,
        tenant_pub_id: str,
        created_by_pub_id: str,
        target_brand: str,
        target_brand_aliases: list[str],
        urls: list[str],
        options: Mapping[str, Any],
        idempotency_key: str | None,
        task_queue: str,
        source_task_queue: str = "geo-platform-v2-source",
    ) -> tuple[dict[str, Any], bool]:
        """→ (task 行, 是否新建)。同 key/体 重放返回 (既存行, False)；同 key 异体 409。"""
        brand = target_brand.strip()
        if not brand:
            raise PostAnalysisInvalid("target_brand 不能为空")
        aliases = [alias.strip() for alias in target_brand_aliases if alias.strip()][:20]
        normalized_options = {
            "verify_facts": bool(options.get("verify_facts", True)),
            "annotate": bool(options.get("annotate", True)),
            "open_investigation": bool(options.get("open_investigation", True)),
        }
        targets = validate_urls(urls, max_urls=self.max_urls_per_task)
        fingerprint = request_fingerprint(
            target_brand=brand,
            target_brand_aliases=aliases,
            urls=targets,
            options=normalized_options,
        )
        task_pub_id = derive_task_pub_id(tenant_pub_id, idempotency_key, fingerprint)
        with self._tenant_conn(tenant_pub_id) as (connection, tenant_id):
            existing = connection.execute(
                "SELECT * FROM platform.post_analysis_task WHERE pub_id=%s", (task_pub_id,)
            ).fetchone()
            if existing is not None:
                self._assert_replay_match(
                    connection,
                    existing,
                    fingerprint=fingerprint,
                )
                return _public_task(existing), False
            workflow_id = workflow_id_for(tenant_pub_id, task_pub_id)
            connection.execute(
                """
                INSERT INTO platform.post_analysis_task
                  (id,pub_id,tenant_id,target_brand,target_brand_aliases,status,url_count,
                   options,idempotency_key,workflow_id,created_by,created_at,updated_at)
                VALUES (gen_random_uuid(),%s,%s,%s,CAST(%s AS jsonb),'queued',%s,
                        CAST(%s AS jsonb),%s,%s,%s,now(),now())
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    task_pub_id,
                    tenant_id,
                    brand,
                    json.dumps(aliases, ensure_ascii=False),
                    len(targets),
                    json.dumps(normalized_options, ensure_ascii=False),
                    idempotency_key,
                    workflow_id,
                    created_by_pub_id,
                ),
            )
            task_row = connection.execute(
                "SELECT * FROM platform.post_analysis_task WHERE pub_id=%s", (task_pub_id,)
            ).fetchone()
            if task_row is None:  # 并发竞态：他事务先插，回读按其内容重放判定
                raise PostAnalysisConflict("idempotency_conflict")
            task_id = str(task_row["id"])
            for target in targets:
                connection.execute(
                    """
                    INSERT INTO platform.post_analysis_item
                      (id,pub_id,task_id,tenant_id,ordinal,url,url_hash,host,status,
                       annotation_status,created_at,updated_at)
                    VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,'pending','pending',
                            now(),now())
                    ON CONFLICT (task_id,url_hash) DO NOTHING
                    """,
                    (
                        derive_item_pub_id(tenant_pub_id, task_pub_id, target.url_hash),
                        task_id,
                        tenant_id,
                        target.ordinal,
                        target.url,
                        target.url_hash,
                        target.host,
                    ),
                )
            connection.execute(
                """
                INSERT INTO integration.workflow_start_command
                  (command_id,tenant_pub_id,workflow_type,workflow_id,task_queue,payload,
                   trace_context)
                VALUES (%s,%s,%s,%s,%s,CAST(%s AS jsonb),CAST(%s AS jsonb))
                """,
                (
                    uuid.uuid4(),
                    tenant_pub_id,
                    WORKFLOW_TYPE,
                    workflow_id,
                    task_queue,
                    json.dumps(
                        {
                            "tenant_pub_id": tenant_pub_id,
                            "task_pub_id": task_pub_id,
                            "source_task_queue": source_task_queue,
                        },
                        separators=(",", ":"),
                    ),
                    json.dumps({}, separators=(",", ":")),
                ),
            )
            connection.execute(
                """
                UPDATE platform.post_analysis_task
                SET options = options || CAST(%s AS jsonb)
                WHERE pub_id=%s
                """,
                (json.dumps({"fingerprint": fingerprint}), task_pub_id),
            )
            connection.commit()
            return _public_task(task_row), True

    def _assert_replay_match(
        self,
        connection: psycopg.Connection[Any],
        existing: Mapping[str, Any],
        *,
        fingerprint: str,
    ) -> None:
        stored_options = existing["options"]
        stored_fingerprint = (
            stored_options.get("fingerprint") if isinstance(stored_options, dict) else None
        )
        if stored_fingerprint != fingerprint:
            raise PostAnalysisConflict("idempotency_conflict")

    # -- 查询 -----------------------------------------------------------------

    def list_tasks(
        self, *, tenant_pub_id: str, cursor: str | None, limit: int
    ) -> list[dict[str, Any]]:
        with self._tenant_conn(tenant_pub_id) as (connection, tenant_id):
            rows = connection.execute(
                """
                SELECT * FROM platform.post_analysis_task
                WHERE tenant_id=%s
                  AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [_public_task(row) for row in rows]

    def get_task(self, *, tenant_pub_id: str, task_pub_id: str) -> dict[str, Any]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            row = connection.execute(
                "SELECT * FROM platform.post_analysis_task WHERE pub_id=%s", (task_pub_id,)
            ).fetchone()
            if row is None:
                raise PostAnalysisNotFound("post analysis task not found")
            count_rows = connection.execute(
                """
                SELECT status, count(*) AS n FROM platform.post_analysis_item
                WHERE task_id=%s GROUP BY status
                """,
                (row["id"],),
            ).fetchall()
        counts = {str(item["status"]): int(item["n"]) for item in count_rows}
        options = row["options"] if isinstance(row["options"], dict) else {}
        investigation_pub_id = options.get("investigation_pub_id")
        return {
            **_public_task(row),
            "status_counts": counts,
            "investigation_pub_id": (str(investigation_pub_id) if investigation_pub_id else None),
        }

    def list_items(
        self, *, tenant_pub_id: str, task_pub_id: str, cursor: str | None, limit: int
    ) -> list[dict[str, Any]]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            task_row = connection.execute(
                "SELECT id FROM platform.post_analysis_task WHERE pub_id=%s", (task_pub_id,)
            ).fetchone()
            if task_row is None:
                raise PostAnalysisNotFound("post analysis task not found")
            rows = connection.execute(
                """
                SELECT pub_id,ordinal,url,host,status,annotation_status,analysis,error,
                       created_at,updated_at
                FROM platform.post_analysis_item
                WHERE task_id=%s AND (%s::text IS NULL OR pub_id>%s)
                ORDER BY pub_id LIMIT %s
                """,
                (task_row["id"], cursor, cursor, limit + 1),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            public = _public_item(row)
            analysis = public.pop("analysis", None)
            out.append({**public, **item_list_fields({"analysis": analysis})})
        return out

    def get_item(self, *, tenant_pub_id: str, item_pub_id: str) -> dict[str, Any]:
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            row = connection.execute(
                "SELECT * FROM platform.post_analysis_item WHERE pub_id=%s", (item_pub_id,)
            ).fetchone()
            if row is None:
                raise PostAnalysisNotFound("post analysis item not found")
            screenshot_cas_key = row["screenshot_cas_key"]
            annotated_cas_key = row["annotated_cas_key"]
            screenshot_asset = (
                self._resolve_asset_ref(connection, tenant_pub_id, str(screenshot_cas_key))
                if screenshot_cas_key
                else None
            )
            annotated_asset = (
                self._resolve_asset_ref(connection, tenant_pub_id, str(annotated_cas_key))
                if annotated_cas_key
                else None
            )
        public = _public_item(row)
        public["has_screenshot"] = bool(public.pop("screenshot_cas_key", None))
        public["has_annotated"] = bool(public.pop("annotated_cas_key", None))
        public.pop("text_cas_key", None)
        public["screenshot_asset"] = screenshot_asset
        public["annotated_asset"] = annotated_asset
        return public

    @staticmethod
    def _resolve_asset_ref(
        connection: psycopg.Connection[Any], tenant_pub_id: str, object_key: str
    ) -> dict[str, Any] | None:
        """cas key → evidence_asset 完整性三元组 {sha256, byte_size, mime_type}。

        detail 投影与资产下载（get_item_asset）共用同一查询形状；资产行缺失
        （CAS 引用悬空）→ None，如实降级为"无资产"。
        """
        asset_row = connection.execute(
            """
            SELECT sha256, byte_size, mime_type FROM evidence.evidence_asset
            WHERE tenant_pub_id=%s AND object_key=%s
            """,
            (tenant_pub_id, object_key),
        ).fetchone()
        if asset_row is None:
            return None
        return {
            "sha256": str(asset_row["sha256"]),
            "byte_size": int(asset_row["byte_size"]),
            "mime_type": str(asset_row["mime_type"]),
        }

    def get_item_asset(
        self, *, tenant_pub_id: str, item_pub_id: str, kind: str
    ) -> tuple[bytes, str, str]:
        """→ (bytes, mime_type, sha256)。CAS 引用缺失 → PostAnalysisNotFound。"""
        if kind not in _ASSET_KINDS:
            raise PostAnalysisNotFound("unknown asset kind")
        column = "screenshot_cas_key" if kind == "screenshot" else "annotated_cas_key"
        with self._tenant_conn(tenant_pub_id) as (connection, _tenant_id):
            item_row = connection.execute(
                f"SELECT {column} FROM platform.post_analysis_item WHERE pub_id=%s",
                (item_pub_id,),
            ).fetchone()
            if item_row is None or not item_row[column]:
                raise PostAnalysisNotFound("post analysis asset not found")
            asset_ref = self._resolve_asset_ref(connection, tenant_pub_id, str(item_row[column]))
            object_key = str(item_row[column])
        if asset_ref is None:
            raise PostAnalysisNotFound("post analysis asset not found")
        if self._object_store is None:
            raise PostAnalysisNotFound("object store unavailable")
        payload = self._object_store.get_verified(object_key, asset_ref["sha256"])
        return payload, asset_ref["mime_type"], asset_ref["sha256"]
