from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..tenancy.ids import new_pub_id
from ..tenancy.psycopg import tenant_connection
from .catalog import ResolvedCatalog
from .docx import ParsedDocx
from .providers import ProviderResult, ProviderSubmission, provider_for


class PostingNotFound(LookupError):
    """The posting resource does not exist in the tenant."""


class PostingInvalidState(RuntimeError):
    """The requested posting transition is not safe."""


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "id"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class PostingService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def _conn(self, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            yield connection

    @staticmethod
    def _required(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if row is None:
            raise PostingNotFound("posting resource not found")
        return row

    def _event(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
        event_type: str,
        target_pub_id: str | None = None,
        from_status: str = "",
        to_status: str = "",
        message: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO posting.event
              (pub_id,tenant_pub_id,batch_pub_id,target_pub_id,event_type,
               from_status,to_status,message,payload,actor_pub_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                new_pub_id("pev"),
                tenant_pub_id,
                batch_pub_id,
                target_pub_id,
                event_type,
                from_status,
                to_status,
                message,
                _json(dict(payload or {})),
                actor_pub_id,
            ),
        )

    def _detail(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
    ) -> dict[str, Any]:
        batch = self._required(
            connection.execute(
                "SELECT * FROM posting.batch WHERE tenant_pub_id=%s AND pub_id=%s",
                (tenant_pub_id, batch_pub_id),
            ).fetchone()
        )
        targets = connection.execute(
            """
            SELECT * FROM posting.target
            WHERE tenant_pub_id=%s AND batch_pub_id=%s
            ORDER BY created_at,pub_id
            """,
            (tenant_pub_id, batch_pub_id),
        ).fetchall()
        events = connection.execute(
            """
            SELECT * FROM posting.event
            WHERE tenant_pub_id=%s AND batch_pub_id=%s
            ORDER BY created_at,pub_id
            """,
            (tenant_pub_id, batch_pub_id),
        ).fetchall()
        public_batch = _public(batch)
        public_batch.pop("idempotency_key_sha256", None)
        public_batch.pop("content_html", None)
        return {
            **public_batch,
            "targets": [_public(row) for row in targets],
            "events": [_public(row) for row in events],
        }

    def create_batch(
        self,
        *,
        tenant_pub_id: str,
        actor_pub_id: str,
        idempotency_key: str,
        document: ParsedDocx,
        catalog: ResolvedCatalog,
        title: str,
        customer_name: str,
        release_time: date | None,
        auto_submit: bool,
        confirm_spend: bool,
        max_total_amount: Decimal | None,
        note: str,
        sop_project_pub_id: str | None = None,
        article_version_pub_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        key_sha256 = sha256(idempotency_key.encode()).hexdigest()
        quoted_total = sum(
            (target.quoted_price for target in catalog.targets),
            start=Decimal("0.00"),
        )
        if auto_submit:
            if not confirm_spend:
                raise PostingInvalidState("spend confirmation is required")
            if max_total_amount is None or max_total_amount < quoted_total:
                raise PostingInvalidState("spend limit is below the quoted total")
        with self._conn(tenant_pub_id) as connection:
            existing = connection.execute(
                """
                SELECT pub_id FROM posting.batch
                WHERE tenant_pub_id=%s AND idempotency_key_sha256=%s
                """,
                (tenant_pub_id, key_sha256),
            ).fetchone()
            if existing is not None:
                return (
                    self._detail(
                        connection,
                        tenant_pub_id=tenant_pub_id,
                        batch_pub_id=str(existing["pub_id"]),
                    ),
                    False,
                )
            batch_pub_id = new_pub_id("pbt")
            batch_status = "draft"
            approval_state = "pending" if auto_submit else "draft"
            connection.execute(
                """
                INSERT INTO posting.batch
                  (pub_id,tenant_pub_id,idempotency_key_sha256,source_filename,
                   source_sha256,catalog_sha256,title,content_text,content_html,image_count,
                   customer_name,release_time,auto_submit,spend_confirmed_at,
                   max_total_amount,quoted_total_amount,status,note,created_by_pub_id,
                   sop_project_pub_id,article_version_pub_id,approval_state,
                   approval_requested_by_pub_id)
                VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  CASE WHEN %s THEN now() ELSE NULL END,%s,%s,%s,%s,%s,%s,%s,%s,
                  CASE WHEN %s THEN %s ELSE NULL END
                )
                """,
                (
                    batch_pub_id,
                    tenant_pub_id,
                    key_sha256,
                    document.filename,
                    document.sha256,
                    catalog.sha256,
                    title,
                    document.content_text,
                    document.content_html,
                    document.image_count,
                    customer_name,
                    release_time,
                    auto_submit,
                    confirm_spend,
                    max_total_amount,
                    quoted_total,
                    batch_status,
                    note,
                    actor_pub_id,
                    sop_project_pub_id,
                    article_version_pub_id,
                    approval_state,
                    auto_submit,
                    actor_pub_id,
                ),
            )
            target_status = "selected"
            for target in catalog.targets:
                target_pub_id = new_pub_id("ptg")
                connection.execute(
                    """
                    INSERT INTO posting.target
                      (pub_id,tenant_pub_id,batch_pub_id,catalog_type,provider,
                       media_name,media_platform,provider_media_id,quoted_price,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        target_pub_id,
                        tenant_pub_id,
                        batch_pub_id,
                        target.catalog_type,
                        target.provider,
                        target.media_name,
                        target.media_platform,
                        target.provider_media_id,
                        target.quoted_price,
                        target_status,
                    ),
                )
                self._event(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    batch_pub_id=batch_pub_id,
                    target_pub_id=target_pub_id,
                    actor_pub_id=actor_pub_id,
                    event_type="target.selected",
                    to_status=target_status,
                    message=f"{target.provider} / {target.media_name}",
                    payload={
                        "catalog_type": target.catalog_type,
                        "quoted_price": str(target.quoted_price),
                    },
                )
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="batch.created",
                to_status=batch_status,
                message=f"已解析 {document.filename}，共 {len(catalog.targets)} 个投放目标",
                payload={
                    "source_sha256": document.sha256,
                    "catalog_sha256": catalog.sha256,
                    "image_count": document.image_count,
                    "quoted_total_amount": str(quoted_total),
                    "approval_state": approval_state,
                },
            )
            if auto_submit:
                self._event(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    batch_pub_id=batch_pub_id,
                    actor_pub_id=actor_pub_id,
                    event_type="approval.requested",
                    from_status="draft",
                    to_status="pending",
                    message="预算已确认，等待独立审核后提交",
                    payload={"max_total_amount": str(max_total_amount)},
                )
            return (
                self._detail(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    batch_pub_id=batch_pub_id,
                ),
                True,
            )

    def list_batches(
        self,
        *,
        tenant_pub_id: str,
        status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT b.*,
                       count(t.pub_id)::integer AS target_count,
                       count(t.pub_id) FILTER (
                         WHERE t.status IN ('submitted','reviewing','published')
                       )::integer AS submitted_count,
                       count(t.pub_id) FILTER (
                         WHERE t.status='published'
                       )::integer AS published_count
                FROM posting.batch b
                LEFT JOIN posting.target t
                  ON t.tenant_pub_id=b.tenant_pub_id AND t.batch_pub_id=b.pub_id
                WHERE b.tenant_pub_id=%s
                  AND (%s::text IS NULL OR b.status=%s)
                GROUP BY b.id
                ORDER BY b.created_at DESC,b.pub_id DESC
                LIMIT %s
                """,
                (tenant_pub_id, status, status, limit),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            item = _public(row)
            item.pop("idempotency_key_sha256", None)
            item.pop("content_html", None)
            content_text = str(item.pop("content_text", ""))
            item["content_excerpt"] = content_text[:300]
            summaries.append(item)
        return summaries

    def get_batch(self, *, tenant_pub_id: str, batch_pub_id: str) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            return self._detail(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )

    def enqueue_batch(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
        max_total_amount: Decimal,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            batch = self._required(
                connection.execute(
                    """
                    SELECT * FROM posting.batch
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    FOR UPDATE
                    """,
                    (tenant_pub_id, batch_pub_id),
                ).fetchone()
            )
            if str(batch["status"]) in {"published", "canceled"}:
                raise PostingInvalidState("posting batch is terminal")
            if str(batch.get("approval_state", "draft")) == "approved":
                raise PostingInvalidState("posting batch is already approved")
            quoted_total = Decimal(str(batch["quoted_total_amount"]))
            if max_total_amount < quoted_total:
                raise PostingInvalidState("spend limit is below the quoted total")
            prior = str(batch["status"])
            connection.execute(
                """
                UPDATE posting.batch
                SET auto_submit=true,spend_confirmed_at=now(),max_total_amount=%s,
                    status='draft',approval_state='pending',
                    approval_requested_by_pub_id=%s,approved_by_pub_id=NULL,
                    approved_at=NULL,updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (max_total_amount, actor_pub_id, tenant_pub_id, batch_pub_id),
            )
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="approval.requested",
                from_status=prior,
                to_status="pending",
                message="已确认预算，等待独立审核后提交",
                payload={"max_total_amount": str(max_total_amount)},
            )
            return self._detail(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )

    def decide_approval(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
        approve: bool,
        rationale: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            batch = self._required(
                connection.execute(
                    """
                    SELECT * FROM posting.batch
                    WHERE tenant_pub_id=%s AND pub_id=%s FOR UPDATE
                    """,
                    (tenant_pub_id, batch_pub_id),
                ).fetchone()
            )
            if str(batch["approval_state"]) != "pending":
                raise PostingInvalidState("posting approval is not pending")
            requester = str(batch["approval_requested_by_pub_id"] or "")
            if requester == actor_pub_id:
                raise PostingInvalidState("posting creator cannot approve their own request")
            if approve:
                if batch["spend_confirmed_at"] is None or batch["max_total_amount"] is None:
                    raise PostingInvalidState("spend confirmation is required")
                quoted_total = Decimal(str(batch["quoted_total_amount"]))
                if Decimal(str(batch["max_total_amount"])) < quoted_total:
                    raise PostingInvalidState("spend limit is below the quoted total")
                tenant = self._required(
                    connection.execute(
                        "SELECT environment FROM platform.tenant WHERE pub_id=%s",
                        (tenant_pub_id,),
                    ).fetchone()
                )
                training = str(tenant["environment"]) == "training"
                if training:
                    connection.execute(
                        """
                        UPDATE posting.target
                        SET status='submitted',submitted_at=COALESCE(submitted_at,now()),
                            provider_message='训练环境模拟提交：未调用供应商、未产生费用',
                            updated_at=now()
                        WHERE tenant_pub_id=%s AND batch_pub_id=%s AND status='selected'
                        """,
                        (tenant_pub_id, batch_pub_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE posting.target SET status='queued',updated_at=now()
                        WHERE tenant_pub_id=%s AND batch_pub_id=%s
                          AND status IN (
                            'selected','balance_insufficient','provider_session_expired','failed'
                          )
                        """,
                        (tenant_pub_id, batch_pub_id),
                    )
                connection.execute(
                    """
                    UPDATE posting.batch
                    SET approval_state='approved',approved_by_pub_id=%s,
                        approved_at=now(),status=%s,updated_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (
                        actor_pub_id,
                        "submitted" if training else "queued",
                        tenant_pub_id,
                        batch_pub_id,
                    ),
                )
                event_type = "approval.approved"
                to_state = "approved"
                message = (
                    f"{rationale}（训练环境仅模拟提交）"
                    if training
                    else (rationale or "独立审核已通过，进入发帖队列")
                )
            else:
                connection.execute(
                    """
                    UPDATE posting.batch
                    SET approval_state='rejected',approved_by_pub_id=%s,
                        approved_at=now(),status='draft',updated_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (actor_pub_id, tenant_pub_id, batch_pub_id),
                )
                event_type = "approval.rejected"
                to_state = "rejected"
                message = rationale
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                actor_pub_id=actor_pub_id,
                event_type=event_type,
                from_status="pending",
                to_status=to_state,
                message=message,
            )
            return self._detail(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )

    def backfill_target(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        target_pub_id: str,
        actor_pub_id: str,
        status: str,
        public_url: str,
        provider_message: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            current = self._required(
                connection.execute(
                    """
                    SELECT status FROM posting.target
                    WHERE tenant_pub_id=%s AND batch_pub_id=%s AND pub_id=%s FOR UPDATE
                    """,
                    (tenant_pub_id, batch_pub_id, target_pub_id),
                ).fetchone()
            )
            if status == "published" and not public_url:
                raise PostingInvalidState("published target requires public URL")
            connection.execute(
                """
                UPDATE posting.target
                SET status=%s,public_url=%s,provider_message=%s,
                    submitted_at=CASE WHEN %s IN ('submitted','reviewing','published')
                                      THEN COALESCE(submitted_at,now()) ELSE submitted_at END,
                    published_at=CASE WHEN %s='published' THEN COALESCE(published_at,now())
                                      ELSE published_at END,
                    updated_at=now()
                WHERE tenant_pub_id=%s AND batch_pub_id=%s AND pub_id=%s
                """,
                (
                    status,
                    public_url,
                    provider_message,
                    status,
                    status,
                    tenant_pub_id,
                    batch_pub_id,
                    target_pub_id,
                ),
            )
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=target_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="target.backfilled",
                from_status=str(current["status"]),
                to_status=status,
                message=provider_message or "运营人工回填目标状态",
                payload={"public_url": public_url},
            )
            self._recompute_batch(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )
            return self._detail(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )

    def create_attribution(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        target_pub_id: str | None,
        sop_publication_pub_id: str | None,
        retest_run_pub_id: str | None,
        public_url: str,
        relation_type: str,
        evidence_sha256: str | None,
        note: str,
        actor_pub_id: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            self._required(
                connection.execute(
                    "SELECT pub_id FROM posting.batch WHERE tenant_pub_id=%s AND pub_id=%s",
                    (tenant_pub_id, batch_pub_id),
                ).fetchone()
            )
            if target_pub_id is not None:
                self._required(
                    connection.execute(
                        """
                        SELECT pub_id FROM posting.target
                        WHERE tenant_pub_id=%s AND batch_pub_id=%s AND pub_id=%s
                        """,
                        (tenant_pub_id, batch_pub_id, target_pub_id),
                    ).fetchone()
                )
            pub_id = new_pub_id("pat")
            row = connection.execute(
                """
                INSERT INTO posting.attribution
                  (pub_id,tenant_pub_id,batch_pub_id,target_pub_id,sop_publication_pub_id,
                   retest_run_pub_id,public_url,relation_type,evidence_sha256,note,
                   created_by_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (
                  tenant_pub_id,batch_pub_id,target_pub_id,sop_publication_pub_id,
                  retest_run_pub_id,relation_type
                ) DO UPDATE SET public_url=EXCLUDED.public_url,
                                evidence_sha256=EXCLUDED.evidence_sha256,
                                note=EXCLUDED.note
                RETURNING *
                """,
                (
                    pub_id,
                    tenant_pub_id,
                    batch_pub_id,
                    target_pub_id,
                    sop_publication_pub_id,
                    retest_run_pub_id,
                    public_url,
                    relation_type,
                    evidence_sha256,
                    note,
                    actor_pub_id,
                ),
            ).fetchone()
            assert row is not None
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=target_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="attribution.recorded",
                message=f"已记录 {relation_type} 归因关系",
                payload={"attribution_pub_id": row["pub_id"]},
            )
            return _public(row)

    def list_attributions(self, *, tenant_pub_id: str, batch_pub_id: str) -> list[dict[str, Any]]:
        with self._conn(tenant_pub_id) as connection:
            rows = connection.execute(
                """
                SELECT * FROM posting.attribution
                WHERE tenant_pub_id=%s AND batch_pub_id=%s
                ORDER BY created_at,pub_id
                """,
                (tenant_pub_id, batch_pub_id),
            ).fetchall()
        return [_public(row) for row in rows]

    def _claim_target(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
    ) -> dict[str, Any] | None:
        with self._conn(tenant_pub_id) as connection:
            row = connection.execute(
                """
                SELECT t.*,b.title,b.content_html,b.customer_name,b.release_time
                FROM posting.target t
                JOIN posting.batch b
                  ON b.tenant_pub_id=t.tenant_pub_id AND b.pub_id=t.batch_pub_id
                WHERE t.tenant_pub_id=%s AND t.batch_pub_id=%s AND t.status='queued'
                  AND b.approval_state='approved'
                ORDER BY t.created_at,t.pub_id
                FOR UPDATE OF t SKIP LOCKED
                LIMIT 1
                """,
                (tenant_pub_id, batch_pub_id),
            ).fetchone()
            if row is None:
                return None
            target_pub_id = str(row["pub_id"])
            connection.execute(
                """
                UPDATE posting.target SET status='submitting',updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, target_pub_id),
            )
            connection.execute(
                """
                UPDATE posting.batch SET status='processing',updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                  AND status IN ('queued','processing')
                """,
                (tenant_pub_id, batch_pub_id),
            )
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=target_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="target.submitting",
                from_status="queued",
                to_status="submitting",
                message=f"正在提交 {row['provider']} / {row['media_name']}",
            )
            claimed = _public(row)
            claimed["status"] = "submitting"
            return claimed

    def _recompute_batch(
        self,
        connection: psycopg.Connection[Any],
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
    ) -> str:
        rows = connection.execute(
            """
            SELECT status FROM posting.target
            WHERE tenant_pub_id=%s AND batch_pub_id=%s
            """,
            (tenant_pub_id, batch_pub_id),
        ).fetchall()
        statuses = {str(row["status"]) for row in rows}
        delivered = {"submitted", "reviewing", "published"}
        active = {"queued", "submitting"}
        blockers = {
            "balance_insufficient",
            "provider_session_expired",
            "provider_confirmation_required",
            "unsupported_provider",
        }
        failures = {"rejected", "failed"}
        if statuses == {"published"}:
            batch_status = "published"
        elif statuses and statuses <= delivered:
            batch_status = "submitted"
        elif statuses & delivered:
            batch_status = "partially_submitted"
        elif statuses & active:
            batch_status = "processing"
        elif statuses and statuses <= blockers:
            batch_status = "blocked"
        elif statuses & failures:
            batch_status = "failed"
        else:
            batch_status = "draft"
        connection.execute(
            """
            UPDATE posting.batch SET status=%s,updated_at=now()
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (batch_status, tenant_pub_id, batch_pub_id),
        )
        return batch_status

    def _record_result(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        target_pub_id: str,
        actor_pub_id: str,
        from_status: str,
        result: ProviderResult,
    ) -> None:
        with self._conn(tenant_pub_id) as connection:
            current = self._required(
                connection.execute(
                    """
                    SELECT status FROM posting.target
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    FOR UPDATE
                    """,
                    (tenant_pub_id, target_pub_id),
                ).fetchone()
            )
            current_status = str(current["status"])
            submitted = result.status in {"submitted", "reviewing", "published"}
            connection.execute(
                """
                UPDATE posting.target
                SET status=%s,provider_message=%s,
                    external_order_id=CASE WHEN %s<>'' THEN %s ELSE external_order_id END,
                    public_url=CASE WHEN %s<>'' THEN %s ELSE public_url END,
                    submitted_at=CASE
                      WHEN %s AND submitted_at IS NULL THEN now() ELSE submitted_at END,
                    published_at=CASE
                      WHEN %s='published' AND published_at IS NULL THEN now() ELSE published_at END,
                    updated_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (
                    result.status,
                    result.message,
                    result.external_order_id,
                    result.external_order_id,
                    result.public_url,
                    result.public_url,
                    submitted,
                    result.status,
                    tenant_pub_id,
                    target_pub_id,
                ),
            )
            self._event(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=target_pub_id,
                actor_pub_id=actor_pub_id,
                event_type="target.status_changed",
                from_status=current_status or from_status,
                to_status=result.status,
                message=result.message,
                payload={
                    "external_order_id": result.external_order_id,
                    "public_url": result.public_url,
                },
            )
            self._recompute_batch(
                connection,
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
            )

    def execute_batch(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
    ) -> None:
        while True:
            target = self._claim_target(
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                actor_pub_id=actor_pub_id,
            )
            if target is None:
                break
            provider = provider_for(str(target["provider"]))
            result = provider.submit(
                ProviderSubmission(
                    provider=str(target["provider"]),
                    catalog_type=str(target["catalog_type"]),
                    provider_media_id=str(target["provider_media_id"]),
                    media_name=str(target["media_name"]),
                    title=str(target["title"]),
                    content_html=str(target["content_html"]),
                    customer_name=str(target["customer_name"]),
                    release_time=(
                        target["release_time"] if isinstance(target["release_time"], date) else None
                    ),
                )
            )
            self._record_result(
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=str(target["pub_id"]),
                actor_pub_id=actor_pub_id,
                from_status="submitting",
                result=result,
            )

    def refresh_batch(
        self,
        *,
        tenant_pub_id: str,
        batch_pub_id: str,
        actor_pub_id: str,
    ) -> dict[str, Any]:
        with self._conn(tenant_pub_id) as connection:
            batch = self._required(
                connection.execute(
                    """
                    SELECT title FROM posting.batch
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, batch_pub_id),
                ).fetchone()
            )
            targets = connection.execute(
                """
                SELECT * FROM posting.target
                WHERE tenant_pub_id=%s AND batch_pub_id=%s
                  AND status IN ('submitted','reviewing')
                ORDER BY created_at,pub_id
                """,
                (tenant_pub_id, batch_pub_id),
            ).fetchall()
        for target in targets:
            result = provider_for(str(target["provider"])).refresh(
                catalog_type=str(target["catalog_type"]),
                external_order_id=str(target["external_order_id"]),
                media_name=str(target["media_name"]),
                title=str(batch["title"]),
            )
            if result is None:
                continue
            self._record_result(
                tenant_pub_id=tenant_pub_id,
                batch_pub_id=batch_pub_id,
                target_pub_id=str(target["pub_id"]),
                actor_pub_id=actor_pub_id,
                from_status=str(target["status"]),
                result=result,
            )
        return self.get_batch(tenant_pub_id=tenant_pub_id, batch_pub_id=batch_pub_id)

    def debug_target(self, target: Mapping[str, Any]) -> dict[str, Any]:
        """Small typed helper retained for adapter diagnostics without secrets."""
        return asdict(
            ProviderSubmission(
                provider=str(target["provider"]),
                catalog_type=str(target["catalog_type"]),
                provider_media_id=str(target["provider_media_id"]),
                media_name=str(target["media_name"]),
                title=str(target["title"]),
                content_html="",
                customer_name="",
                release_time=None,
            )
        )
