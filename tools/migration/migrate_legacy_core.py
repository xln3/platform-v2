"""Idempotently migrate legacy identity, catalog and raw answers into V2.

The source is always opened read-only. Password hashes, sessions, OTP values and
browser material are intentionally outside this migration track.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from geo_platform.config import get_settings
from psycopg.rows import dict_row

SOURCE_SYSTEM = "legacy-geosys-sqlite"
# V2 has product roles rather than the legacy tenant ACL labels. Tenant owners
# and administrators retain tenant administration; read-only legacy viewers
# become customer readers; members enter the analyst workspace.
ROLE_MAP = {"owner": "admin", "admin": "admin", "member": "analyst", "viewer": "customer"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(row).encode()).hexdigest()


def deterministic_pub_id(prefix: str, snapshot_hash: str, entity: str, source_pk: str) -> str:
    digest = hashlib.sha256(f"{snapshot_hash}:{entity}:{source_pk}".encode()).digest()[:16]
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = int.from_bytes(digest, "big")
    encoded = []
    for _ in range(26):
        encoded.append(alphabet[value & 31])
        value >>= 5
    suffix = "".join(reversed(encoded))
    return f"{prefix}_{suffix}"


def deterministic_uuid(snapshot_hash: str, entity: str, source_pk: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"geo-v2:{snapshot_hash}:{entity}:{source_pk}")


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _dsn() -> str:
    return get_settings().postgres_dsn.replace("postgresql+psycopg://", "postgresql://")


class CoreMigrator:
    def __init__(self, source: Path, *, dsn: str, inject_failure_after: str | None = None) -> None:
        self.source = source.resolve(strict=True)
        self.snapshot_hash = file_sha256(self.source)
        self.dsn = dsn
        self.run_id = deterministic_uuid(self.snapshot_hash, "migration_run", "core")
        self.run_pub_id = deterministic_pub_id("mig", self.snapshot_hash, "migration_run", "core")
        self.counts: dict[str, dict[str, int]] = {}
        self.inject_failure_after = inject_failure_after

    def _pub(self, prefix: str, entity: str, source_pk: str | int) -> str:
        return deterministic_pub_id(prefix, self.snapshot_hash, entity, str(source_pk))

    def _uuid(self, entity: str, source_pk: str | int) -> uuid.UUID:
        return deterministic_uuid(self.snapshot_hash, entity, str(source_pk))

    def _record_map(
        self,
        target: psycopg.Connection[Any],
        *,
        entity: str,
        source_pk: str | int,
        source_row: dict[str, Any],
        target_pub_id: str,
    ) -> bool:
        digest = row_hash(source_row)
        existing = target.execute(
            """
            SELECT source_hash,target_pub_id FROM integration.legacy_id_map
            WHERE run_id=%s AND source_system=%s AND entity_type=%s AND source_pk=%s
            """,
            (self.run_id, SOURCE_SYSTEM, entity, str(source_pk)),
        ).fetchone()
        if existing:
            if existing["source_hash"] != digest or existing["target_pub_id"] != target_pub_id:
                raise RuntimeError(f"source drift detected for {entity}")
            return False
        target.execute(
            """
            INSERT INTO integration.legacy_id_map
              (id,run_id,source_system,entity_type,source_pk,source_hash,target_pub_id,state,migrated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'migrated',now())
            """,
            (
                self._uuid(f"map:{entity}", source_pk),
                self.run_id,
                SOURCE_SYSTEM,
                entity,
                str(source_pk),
                digest,
                target_pub_id,
            ),
        )
        return True

    def _watermark(
        self,
        target: psycopg.Connection[Any],
        entity: str,
        last_pk: str | int | None,
        seen: int,
        written: int,
    ) -> None:
        target.execute(
            """
            INSERT INTO integration.migration_watermark
              (id,run_id,entity_type,last_source_pk,rows_seen,rows_written,rows_skipped,
               rows_failed,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,0,now())
            ON CONFLICT (run_id,entity_type) DO UPDATE SET
              last_source_pk=excluded.last_source_pk,rows_seen=excluded.rows_seen,
              rows_written=excluded.rows_written,rows_skipped=excluded.rows_skipped,
              rows_failed=0,updated_at=now()
            """,
            (
                self._uuid("watermark", entity),
                self.run_id,
                entity,
                str(last_pk) if last_pk is not None else None,
                seen,
                written,
                seen - written,
            ),
        )
        self.counts[entity] = {"seen": seen, "written": written, "skipped": seen - written}

    def run(self) -> dict[str, Any]:
        source_uri = f"file:{self.source}?mode=ro"
        with (
            sqlite3.connect(source_uri, uri=True) as source,
            psycopg.connect(self.dsn, row_factory=dict_row) as target,
        ):
            source.row_factory = sqlite3.Row
            target.execute(
                """
                INSERT INTO integration.migration_run
                  (id,pub_id,source_system,source_snapshot_sha256,source_snapshot_at,state,
                   started_at,summary)
                VALUES (%s,%s,%s,%s,%s,'running',now(),'{}'::jsonb)
                ON CONFLICT (source_system,source_snapshot_sha256) DO UPDATE
                  SET state='running',error_code=NULL
                """,
                (
                    self.run_id,
                    self.run_pub_id,
                    SOURCE_SYSTEM,
                    self.snapshot_hash,
                    datetime.fromtimestamp(self.source.stat().st_mtime, UTC),
                ),
            )
            self._migrate_identity(source, target)
            target.commit()
            self._maybe_inject_failure("identity")
            projects = self._migrate_catalog(source, target)
            target.commit()
            self._maybe_inject_failure("catalog")
            self._migrate_collection_history(source, target, projects)
            target.commit()
            self._maybe_inject_failure("collection_history")
            self._migrate_answers(source, target, projects)
            target.commit()
            self._maybe_inject_failure("answers")
            summary = {
                "schema_version": "1.0",
                "source_snapshot_sha256": self.snapshot_hash,
                "run_pub_id": self.run_pub_id,
                "excluded": ["password_hash", "session", "otp", "browser_profile"],
                "counts": self.counts,
            }
            target.execute(
                """
                UPDATE integration.migration_run
                SET state='completed',completed_at=now(),summary=%s::jsonb
                WHERE id=%s
                """,
                (_canonical(summary), self.run_id),
            )
        return summary

    def _maybe_inject_failure(self, phase: str) -> None:
        if self.inject_failure_after == phase:
            raise RuntimeError(f"injected migration interruption after {phase}")

    def _migrate_identity(
        self, source: sqlite3.Connection, target: psycopg.Connection[Any]
    ) -> None:
        tenants = source.execute(
            "SELECT id,pub_id,name,created_at FROM tenant ORDER BY id"
        ).fetchall()
        written = 0
        for raw in tenants:
            row = dict(raw)
            pub = self._pub("tnt", "tenant", row["id"])
            target.execute(
                """
                INSERT INTO platform.tenant(id,pub_id,name,state,created_at,updated_at)
                VALUES (%s,%s,%s,'active',%s,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("tenant", row["id"]),
                    pub,
                    row["name"],
                    parse_time(row["created_at"]),
                    parse_time(row["created_at"]),
                ),
            )
            written += int(
                self._record_map(
                    target, entity="tenant", source_pk=row["id"], source_row=row, target_pub_id=pub
                )
            )
        self._watermark(
            target, "tenant", tenants[-1]["id"] if tenants else None, len(tenants), written
        )

        users = source.execute(
            "SELECT id,pub_id,email,display_name,created_at FROM app_user ORDER BY id"
        ).fetchall()
        written = 0
        for raw in users:
            row = dict(raw)
            pub = self._pub("usr", "app_user", row["id"])
            subject_digest = hashlib.sha256(str(row["email"]).casefold().encode()).hexdigest()
            target.execute(
                """
                INSERT INTO platform.app_user
                  (id,pub_id,subject,display_name,is_service_account,disabled_at,created_at)
                VALUES (%s,%s,%s,%s,false,NULL,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("app_user", row["id"]),
                    pub,
                    f"legacy-metadata:{subject_digest}",
                    row["display_name"] or "Migrated user",
                    parse_time(row["created_at"]),
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="app_user",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(target, "app_user", users[-1]["id"] if users else None, len(users), written)

        memberships = source.execute(
            "SELECT id,tenant_id,user_id,role,created_at FROM membership ORDER BY id"
        ).fetchall()
        written = 0
        for raw in memberships:
            row = dict(raw)
            if row["role"] not in ROLE_MAP:
                raise RuntimeError("unknown legacy role")
            pub = self._pub("mem", "membership", row["id"])
            target.execute(
                """
                INSERT INTO platform.membership
                  (id,pub_id,tenant_id,user_id,role,state,revoked_at,created_at)
                VALUES (%s,%s,%s,%s,%s,'active',NULL,%s)
                ON CONFLICT (pub_id) DO UPDATE
                SET role=EXCLUDED.role
                """,
                (
                    self._uuid("membership", row["id"]),
                    pub,
                    self._uuid("tenant", row["tenant_id"]),
                    self._uuid("app_user", row["user_id"]),
                    ROLE_MAP[row["role"]],
                    parse_time(row["created_at"]),
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="membership",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target,
            "membership",
            memberships[-1]["id"] if memberships else None,
            len(memberships),
            written,
        )

    def _migrate_catalog(
        self, source: sqlite3.Connection, target: psycopg.Connection[Any]
    ) -> dict[int, dict[str, Any]]:
        customers = source.execute(
            "SELECT id,pub_id,tenant_id,name,created_at,updated_at FROM customer ORDER BY id"
        ).fetchall()
        written = 0
        for raw in customers:
            row = dict(raw)
            pub = self._pub("cus", "customer", row["id"])
            target.execute(
                """
                INSERT INTO platform.customer
                  (id,pub_id,tenant_id,version,created_at,updated_at,name,external_ref)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("customer", row["id"]),
                    pub,
                    self._uuid("tenant", row["tenant_id"]),
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    row["name"],
                    f"legacy:{row['pub_id']}",
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="customer",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target, "customer", customers[-1]["id"] if customers else None, len(customers), written
        )

        configs = source.execute(
            """
            SELECT mc.*,b.customer_id,b.name AS brand_name,b.aliases_json,b.own_domains_json
            FROM monitoring_config mc JOIN brand b ON b.id=mc.brand_id ORDER BY mc.id
            """
        ).fetchall()
        projects: dict[int, dict[str, Any]] = {}
        written = 0
        config_written = 0
        brand_written = 0
        for raw in configs:
            row = dict(raw)
            config_id = int(row["id"])
            project_pub = self._pub("prj", "project", config_id)
            project_id = self._uuid("project", config_id)
            tenant_id = self._uuid("tenant", row["tenant_id"])
            target.execute(
                """
                INSERT INTO platform.project
                  (id,pub_id,tenant_id,version,created_at,updated_at,customer_id,name,state)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    project_id,
                    project_pub,
                    tenant_id,
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    self._uuid("customer", row["customer_id"]),
                    row["name"],
                    "active" if row["enabled"] else "paused",
                ),
            )
            brand_entity = f"brand_project:{config_id}"
            brand_pub = self._pub("brd", brand_entity, row["brand_id"])
            domains = [x for x in json.loads(row["own_domains_json"] or "[]") if isinstance(x, str)]
            target.execute(
                """
                INSERT INTO platform.brand
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,name,website)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid(brand_entity, row["brand_id"]),
                    brand_pub,
                    tenant_id,
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    project_id,
                    row["brand_name"],
                    domains[0] if domains else None,
                ),
            )
            for index, alias in enumerate(json.loads(row["aliases_json"] or "[]")):
                if not isinstance(alias, str) or not alias.strip():
                    continue
                alias_pk = f"{row['brand_id']}:{config_id}:{index}"
                target.execute(
                    """
                    INSERT INTO platform.brand_alias
                      (id,pub_id,tenant_id,version,created_at,updated_at,brand_id,value)
                    VALUES (%s,%s,%s,1,%s,%s,%s,%s) ON CONFLICT (pub_id) DO NOTHING
                    """,
                    (
                        self._uuid("brand_alias", alias_pk),
                        self._pub("bal", "brand_alias", alias_pk),
                        tenant_id,
                        parse_time(row["created_at"]),
                        parse_time(row["updated_at"]),
                        self._uuid(brand_entity, row["brand_id"]),
                        alias.strip(),
                    ),
                )
            config_pub = self._pub("cfg", "monitoring_config", config_id)
            config_uuid = self._uuid("monitoring_config", config_id)
            snapshot = {
                "platforms": json.loads(row["platforms_json"] or "[]"),
                "regions": json.loads(row["regions_json"] or "[]"),
                "modes": json.loads(row["modes_json"] or "[]"),
                "cadence": row["cadence"],
                "legacy_enabled": bool(row["enabled"]),
                "migration_activation": "review_required",
            }
            snapshot_json = _canonical(snapshot)
            target.execute(
                """
                INSERT INTO platform.monitoring_config
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,state,current_version)
                VALUES (%s,%s,%s,1,%s,%s,%s,'review_required',1)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    config_uuid,
                    config_pub,
                    tenant_id,
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    project_id,
                ),
            )
            target.execute(
                """
                INSERT INTO platform.monitoring_config_version
                  (id,pub_id,tenant_id,version,created_at,updated_at,config_id,revision,
                   effective_at,frozen_at,snapshot_json,snapshot_hash)
                VALUES (%s,%s,%s,1,%s,%s,%s,1,%s,%s,%s,%s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("monitoring_config_version", config_id),
                    self._pub("cfv", "monitoring_config_version", config_id),
                    tenant_id,
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    config_uuid,
                    parse_time(row["updated_at"]),
                    parse_time(row["updated_at"]),
                    snapshot_json,
                    hashlib.sha256(snapshot_json.encode()).hexdigest(),
                ),
            )
            group_id = self._uuid("query_group", config_id)
            target.execute(
                """
                INSERT INTO platform.query_group
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,name)
                VALUES (%s,%s,%s,1,%s,%s,%s,'Legacy default')
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    group_id,
                    self._pub("qgr", "query_group", config_id),
                    tenant_id,
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    project_id,
                ),
            )
            projects[config_id] = {
                "pub_id": project_pub,
                "tenant_pub_id": self._pub("tnt", "tenant", row["tenant_id"]),
                "tenant_id": tenant_id,
                "brand": row["brand_name"],
                "brand_id": row["brand_id"],
                "own_domains": tuple(domain.lower() for domain in domains),
                "group_id": group_id,
            }
            written += int(
                self._record_map(
                    target,
                    entity="project",
                    source_pk=config_id,
                    source_row={
                        key: row[key]
                        for key in (
                            "id",
                            "pub_id",
                            "tenant_id",
                            "brand_id",
                            "name",
                            "platforms_json",
                            "regions_json",
                            "cadence",
                            "enabled",
                            "modes_json",
                            "created_at",
                            "updated_at",
                        )
                    },
                    target_pub_id=project_pub,
                )
            )
            config_written += int(
                self._record_map(
                    target,
                    entity="monitoring_config",
                    source_pk=config_id,
                    source_row={
                        key: row[key]
                        for key in (
                            "id",
                            "pub_id",
                            "tenant_id",
                            "name",
                            "platforms_json",
                            "regions_json",
                            "cadence",
                            "enabled",
                            "modes_json",
                            "created_at",
                            "updated_at",
                        )
                    },
                    target_pub_id=config_pub,
                )
            )
            brand_written += int(
                self._record_map(
                    target,
                    entity=brand_entity,
                    source_pk=row["brand_id"],
                    source_row={
                        "brand_id": row["brand_id"],
                        "project_source_id": config_id,
                        "name": row["brand_name"],
                        "aliases_json": row["aliases_json"],
                        "own_domains_json": row["own_domains_json"],
                    },
                    target_pub_id=brand_pub,
                )
            )
        self._watermark(
            target, "project", configs[-1]["id"] if configs else None, len(configs), written
        )
        self._watermark(
            target,
            "monitoring_config",
            configs[-1]["id"] if configs else None,
            len(configs),
            config_written,
        )
        self._watermark(
            target,
            "brand_project",
            configs[-1]["id"] if configs else None,
            len(configs),
            brand_written,
        )

        competitors = source.execute(
            """
            SELECT c.id,c.pub_id,c.tenant_id,c.brand_id,c.name,c.created_at,mc.id AS config_id
            FROM competitor c JOIN monitoring_config mc ON mc.brand_id=c.brand_id
            ORDER BY c.id,mc.id
            """
        ).fetchall()
        competitor_written = 0
        for raw in competitors:
            row = dict(raw)
            composite_pk = f"{row['id']}:{row['config_id']}"
            context = projects[int(row["config_id"])]
            pub = self._pub("cmp", "competitor_project", composite_pk)
            target.execute(
                """
                INSERT INTO platform.competitor
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,name,website)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,NULL) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("competitor_project", composite_pk),
                    pub,
                    context["tenant_id"],
                    parse_time(row["created_at"]),
                    parse_time(row["created_at"]),
                    self._uuid("project", row["config_id"]),
                    row["name"],
                ),
            )
            competitor_written += int(
                self._record_map(
                    target,
                    entity="competitor_project",
                    source_pk=composite_pk,
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target,
            "competitor_project",
            competitors[-1]["id"] if competitors else None,
            len(competitors),
            competitor_written,
        )

        queries = source.execute(
            "SELECT id,pub_id,tenant_id,monitoring_config_id,text,enabled,created_at "
            "FROM query_item ORDER BY id"
        ).fetchall()
        written = 0
        for raw in queries:
            row = dict(raw)
            context = projects[int(row["monitoring_config_id"])]
            pub = self._pub("qry", "query_item", row["id"])
            target.execute(
                """
                INSERT INTO platform.query_item
                  (id,pub_id,tenant_id,version,created_at,updated_at,group_id,text,priority)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s) ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("query_item", row["id"]),
                    pub,
                    context["tenant_id"],
                    parse_time(row["created_at"]),
                    parse_time(row["created_at"]),
                    context["group_id"],
                    row["text"],
                    100 if row["enabled"] else 0,
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="query_item",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target, "query_item", queries[-1]["id"] if queries else None, len(queries), written
        )
        return projects

    def _migrate_collection_history(
        self,
        source: sqlite3.Connection,
        target: psycopg.Connection[Any],
        projects: dict[int, dict[str, Any]],
    ) -> None:
        ticks = source.execute(
            """
            SELECT st.id,st.pub_id,st.tenant_id,st.tick_time,st.fire_at,st.state,
                   st.reason,st.created_at,s.monitoring_config_id
            FROM schedule_tick st JOIN schedule s ON s.id=st.schedule_id
            ORDER BY st.id
            """
        ).fetchall()
        written = 0
        for raw in ticks:
            row = dict(raw)
            context = projects[int(row["monitoring_config_id"])]
            stats = source.execute(
                """
                SELECT count(*) AS total,
                       sum(CASE WHEN state='done' THEN 1 ELSE 0 END) AS completed,
                       sum(CASE WHEN state='failed' THEN 1 ELSE 0 END) AS failed
                FROM work_item WHERE schedule_tick_id=?
                """,
                (row["id"],),
            ).fetchone()
            total = int(stats["total"] or 0)
            completed = int(stats["completed"] or 0)
            failed = int(stats["failed"] or 0)
            state = (
                "skipped"
                if row["state"] == "skipped"
                else ("completed_with_failures" if failed or completed < total else "completed")
            )
            pub = self._pub("run", "collection_run", row["id"])
            target.execute(
                """
                INSERT INTO platform.collection_run
                  (id,pub_id,tenant_id,version,created_at,updated_at,project_id,
                   config_version_id,idempotency_key,workflow_id,temporal_run_id,state,
                   total_tasks,completed_tasks,failed_tasks,paused,error_code)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,false,%s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("collection_run", row["id"]),
                    pub,
                    context["tenant_id"],
                    parse_time(row["created_at"]),
                    parse_time(row["tick_time"]),
                    self._uuid("project", row["monitoring_config_id"]),
                    self._uuid("monitoring_config_version", row["monitoring_config_id"]),
                    f"legacy-tick:{row['pub_id']}",
                    f"legacy-history/{pub}",
                    state,
                    total,
                    completed,
                    failed,
                    "legacy_skip" if row["state"] == "skipped" else None,
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="collection_run",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target,
            "collection_run",
            ticks[-1]["id"] if ticks else None,
            len(ticks),
            written,
        )

        tasks = source.execute(
            """
            SELECT w.id,w.pub_id,w.tenant_id,w.schedule_tick_id,w.monitoring_config_id,
                   w.query_item_id,w.query_text,w.platform,w.region,w.mode,w.repeat_idx,
                   w.tick_time,w.state,w.attempts_used,w.priority,w.created_at,w.finished_at,
                   a.response_text
            FROM work_item w LEFT JOIN answer a ON a.work_item_id=w.id
            WHERE w.schedule_tick_id IS NOT NULL
            ORDER BY w.id
            """
        ).fetchall()
        written = 0
        for raw in tasks:
            row = dict(raw)
            context = projects[int(row["monitoring_config_id"])]
            pub = self._pub("tsk", "collection_task", row["id"])
            matrix = _canonical(
                {
                    "query_pub_id": self._pub("qry", "query_item", row["query_item_id"]),
                    "model": row["platform"],
                    "region": row["region"],
                    "mode": row["mode"],
                    "repeat_index": row["repeat_idx"],
                    "historical": True,
                }
            )
            target.execute(
                """
                INSERT INTO platform.collection_task
                  (id,pub_id,tenant_id,version,created_at,updated_at,run_id,business_key,
                   matrix_json,state,attempt_count,answer_text,screenshot_ref,quality_state)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid("collection_task", row["id"]),
                    pub,
                    context["tenant_id"],
                    parse_time(row["created_at"]),
                    parse_time(row["finished_at"] or row["created_at"]),
                    self._uuid("collection_run", row["schedule_tick_id"]),
                    f"legacy-work-item:{row['pub_id']}",
                    matrix,
                    row["state"],
                    int(row["attempts_used"] or 0),
                    row["response_text"],
                    "legacy_rebuilt" if row["state"] == "done" else None,
                ),
            )
            written += int(
                self._record_map(
                    target,
                    entity="collection_task",
                    source_pk=row["id"],
                    source_row=row,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target,
            "collection_task",
            tasks[-1]["id"] if tasks else None,
            len(tasks),
            written,
        )

    def _migrate_answers(
        self,
        source: sqlite3.Connection,
        target: psycopg.Connection[Any],
        projects: dict[int, dict[str, Any]],
    ) -> None:
        answers = source.execute(
            """
            SELECT a.*,w.monitoring_config_id,w.query_item_id
            FROM answer a JOIN work_item w ON w.id=a.work_item_id ORDER BY a.id
            """
        ).fetchall()
        written = 0
        for raw in answers:
            row = dict(raw)
            context = projects[int(row["monitoring_config_id"])]
            pub = self._pub("ans", "answer", row["id"])
            target.execute(
                """
                INSERT INTO analytics.answer
                  (pub_id,tenant_pub_id,project_pub_id,query_pub_id,query_text,response_text,
                   model,region,mode,eligible,degraded,channel,adapter_version,capture_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'api','legacy-migration-v1',%s)
                ON CONFLICT (tenant_pub_id,pub_id) DO NOTHING
                """,
                (
                    pub,
                    context["tenant_pub_id"],
                    context["pub_id"],
                    self._pub("qry", "query_item", row["query_item_id"]),
                    row["query_text"],
                    row["response_text"],
                    row["model_id"] or row["engine"],
                    row["region"],
                    row["mode"],
                    bool(row["eligible"]),
                    bool(row["degraded_flag"]),
                    parse_time(row["tick_time"]),
                ),
            )
            safe_source = {
                key: row[key]
                for key in (
                    "id",
                    "pub_id",
                    "tenant_id",
                    "work_item_id",
                    "engine",
                    "query_text",
                    "region",
                    "mode",
                    "tick_time",
                    "response_text",
                    "model_id",
                    "references_json",
                    "observed_gb_code",
                    "geo_source",
                    "account_source",
                    "captcha_mode",
                    "rate_policy",
                    "degraded_flag",
                    "eligible",
                )
            }
            written += int(
                self._record_map(
                    target,
                    entity="answer",
                    source_pk=row["id"],
                    source_row=safe_source,
                    target_pub_id=pub,
                )
            )
        self._watermark(
            target, "answer", answers[-1]["id"] if answers else None, len(answers), written
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--inject-failure-after",
        choices=("identity", "catalog", "collection_history", "answers"),
    )
    args = parser.parse_args()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = CoreMigrator(
            args.source,
            dsn=_dsn(),
            inject_failure_after=args.inject_failure_after,
        ).run()
    except RuntimeError as exc:
        if not str(exc).startswith("injected migration interruption"):
            raise
        args.evidence.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "result": "interrupted_as_injected",
                    "phase": args.inject_failure_after,
                    "secret_values_included": False,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    else:
        args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
