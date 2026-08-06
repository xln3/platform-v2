"""Idempotently migrate legacy intake tables (客户信息收集表) into V2 platform.

Source: legacy geosys sqlite ``intake_profile`` / ``intake_promo`` /
``intake_trigger_question`` (read-only). Target: V2 postgres ``platform.intake_*``
(models: ``api/geo_platform/intake/models.py``).

Legacy rows belong to legacy ``customer_id``; V2 rows belong to ``project_id``.
The customer→project resolution reuses the core migration's mapping
(``integration.legacy_id_map`` written by ``migrate_legacy_core.py``): the legacy
customer row maps to a V2 customer, and the primary project is the migrated
project with the smallest legacy monitoring_config source_pk for that customer.
Rows whose customer has no V2 counterpart (e.g. legacy customer 4 “中意人寿”,
created after the core migration snapshot) are reported as ``unmapped`` and
skipped unless ``--project-map <customer_id>=<project_pub_id>`` pins them to an
existing V2 project.

Idempotency: target pub_ids are deterministic (same scheme as
``migrate_legacy_core`` — sha256 of the source snapshot + entity + source pk),
so a re-run over an unchanged source finds every row already present and
migrates nothing. Dry-run is the default; ``--apply`` commits.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tools.migration.migrate_legacy_core import (
    SOURCE_SYSTEM,
    _dsn,
    deterministic_pub_id,
    deterministic_uuid,
    file_sha256,
    parse_time,
)

DEFAULT_SOURCE = Path("/home/xln/geo-system/server/geosys.db")

# intake_profile: legacy column → V2 column（标量直映）
PROFILE_SCALAR_MAP = {
    "contact_person": "contact_person",
    "contact_info": "contact_info",
    "website": "website",
    "wechat": "wechat",
    "douyin": "douyin",
    "social_media": "social_media",
    "audience_desc": "audience_desc",
    "business_license_code": "business_license_code",
    "selling_points": "selling_points",
    "filler_name": "filler_name",
    "ad_review_no": "ad_review_no",
    "ad_review_authority": "ad_review_authority",
    "ad_review_expiry": "ad_review_expiry",
    "review_category": "review_category",
}
# intake_profile: legacy *_json TEXT 列 → V2 JSONB 列（去后缀）
PROFILE_JSON_MAP = {
    "goals_json": "goals",
    "audience_type_json": "audience_type",
    "platforms_json": "platforms",
    "regions_json": "regions",
    "trademarks_json": "trademarks",
    "ad_review_doc_types_json": "ad_review_doc_types",
    "evidence_links_json": "evidence_links",
    "licenses_json": "licenses",
    "prefilled_json": "prefilled",
}
# intake_profile: legacy INTEGER 0/1/NULL → V2 boolean
PROFILE_BOOL_MAP = {
    "pre_review_required": "pre_review_required",
    "truth_confirmed": "truth_confirmed",
}
# V2 无对应物、迁移时丢弃并计数的列
PROFILE_DROPPED = ("created_by_user_id",)
TRIGGER_DROPPED = ("claim_pub_id",)

ENTITY_BY_TABLE = {
    "intake_profile": ("intake_profile", "itp"),
    "intake_promo": ("intake_promo", "prm"),
    "intake_trigger_question": ("intake_trigger_question", "trq"),
}


def _parse_json(value: str | None, *, table: str, row_id: int, column: str) -> Any:
    if value is None:
        return [] if column != "prefilled_json" else {}
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{table}#{row_id} column {column} holds invalid JSON: {exc}") from exc


class IntakeMigrator:
    def __init__(
        self,
        source: Path,
        *,
        dsn: str,
        apply: bool,
        project_map: dict[int, str],
        skip_unmapped: bool,
    ) -> None:
        self.source = source.resolve(strict=True)
        self.snapshot_hash = file_sha256(self.source)
        self.dsn = dsn
        self.apply = apply
        self.project_map = project_map
        self.skip_unmapped = skip_unmapped
        self.projects: dict[int, dict[str, Any] | None] = {}
        self.report: dict[str, dict[str, int]] = {}
        self.dropped: dict[str, int] = {key: 0 for key in (*PROFILE_DROPPED, *TRIGGER_DROPPED)}

    def _pub(self, prefix: str, entity: str, source_pk: str | int) -> str:
        return deterministic_pub_id(prefix, self.snapshot_hash, entity, str(source_pk))

    def _uuid(self, entity: str, source_pk: str | int) -> uuid.UUID:
        return deterministic_uuid(self.snapshot_hash, entity, str(source_pk))

    # ── customer → project 归属 ────────────────────────────────────────────
    def _resolve_projects(
        self, source: sqlite3.Connection, target: psycopg.Connection[Any]
    ) -> None:
        customer_ids = [
            row[0]
            for row in source.execute(
                """
                SELECT DISTINCT customer_id FROM (
                  SELECT customer_id FROM intake_profile
                  UNION SELECT customer_id FROM intake_promo
                  UNION SELECT customer_id FROM intake_trigger_question
                ) ORDER BY customer_id
                """
            )
        ]
        for customer_id in customer_ids:
            pinned = self.project_map.get(customer_id)
            if pinned is not None:
                project = target.execute(
                    "SELECT id,pub_id,tenant_id,name FROM platform.project WHERE pub_id=%s",
                    (pinned,),
                ).fetchone()
                if project is None:
                    raise RuntimeError(
                        f"--project-map pins legacy customer {customer_id} to unknown "
                        f"project {pinned}"
                    )
                self.projects[customer_id] = dict(project) | {"via": "cli_override"}
                continue
            customer_pub = target.execute(
                """
                SELECT target_pub_id FROM integration.legacy_id_map
                WHERE source_system=%s AND entity_type='customer' AND source_pk=%s
                ORDER BY migrated_at DESC LIMIT 1
                """,
                (SOURCE_SYSTEM, str(customer_id)),
            ).fetchone()
            if customer_pub is None:
                self.projects[customer_id] = None
                continue
            project = target.execute(
                """
                SELECT p.id,p.pub_id,p.tenant_id,p.name
                FROM integration.legacy_id_map m
                JOIN platform.project p ON p.pub_id=m.target_pub_id
                JOIN platform.customer c ON c.id=p.customer_id
                WHERE m.source_system=%s AND m.entity_type='project'
                  AND c.pub_id=%s
                ORDER BY m.source_pk::int LIMIT 1
                """,
                (SOURCE_SYSTEM, customer_pub["target_pub_id"]),
            ).fetchone()
            self.projects[customer_id] = (
                dict(project) | {"via": "legacy_id_map"} if project is not None else None
            )

    # ── 三张表 ─────────────────────────────────────────────────────────────
    def _migrate_profile(self, source: sqlite3.Connection, target: psycopg.Connection[Any]) -> None:
        rows = source.execute("SELECT * FROM intake_profile ORDER BY id").fetchall()
        counts = {"source": 0, "existing": 0, "migrated": 0, "skipped_unmapped": 0}
        for raw in rows:
            row = dict(raw)
            counts["source"] += 1
            project = self.projects.get(row["customer_id"])
            if project is None:
                counts["skipped_unmapped"] += 1
                continue
            entity, prefix = ENTITY_BY_TABLE["intake_profile"]
            pub = self._pub(prefix, entity, row["id"])
            values: dict[str, Any] = {}
            for old, new in PROFILE_SCALAR_MAP.items():
                values[new] = row[old]
            for old, new in PROFILE_JSON_MAP.items():
                parsed = _parse_json(row[old], table="intake_profile", row_id=row["id"], column=old)
                if not isinstance(parsed, list | dict):
                    raise RuntimeError(
                        f"intake_profile#{row['id']} column {old} must decode to list/dict"
                    )
                values[new] = Jsonb(parsed)
            for old, new in PROFILE_BOOL_MAP.items():
                values[new] = None if row[old] is None else bool(row[old])
            for key in PROFILE_DROPPED:
                if row[key] is not None:
                    self.dropped[key] += 1
            result = target.execute(
                """
                INSERT INTO platform.intake_profile
                  (id,pub_id,tenant_id,project_id,version,created_at,updated_at,
                   contact_person,contact_info,website,wechat,douyin,social_media,
                   audience_desc,business_license_code,selling_points,filler_name,
                   ad_review_no,ad_review_authority,ad_review_expiry,review_category,
                   pre_review_required,truth_confirmed,goals,audience_type,platforms,
                   regions,trademarks,ad_review_doc_types,evidence_links,licenses,prefilled)
                VALUES (%(id)s,%(pub_id)s,%(tenant_id)s,%(project_id)s,1,
                        %(created_at)s,%(updated_at)s,
                        %(contact_person)s,%(contact_info)s,%(website)s,%(wechat)s,
                        %(douyin)s,%(social_media)s,%(audience_desc)s,
                        %(business_license_code)s,%(selling_points)s,%(filler_name)s,
                        %(ad_review_no)s,%(ad_review_authority)s,%(ad_review_expiry)s,
                        %(review_category)s,%(pre_review_required)s,%(truth_confirmed)s,
                        %(goals)s,%(audience_type)s,%(platforms)s,%(regions)s,%(trademarks)s,
                        %(ad_review_doc_types)s,%(evidence_links)s,%(licenses)s,%(prefilled)s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                {
                    "id": self._uuid(entity, row["id"]),
                    "pub_id": pub,
                    "tenant_id": project["tenant_id"],
                    "project_id": project["id"],
                    "created_at": parse_time(row["created_at"]),
                    "updated_at": parse_time(row["updated_at"]),
                    **values,
                },
            )
            if result.rowcount:
                counts["migrated"] += 1
            else:
                counts["existing"] += 1
        self.report["intake_profile"] = counts

    def _migrate_promo(self, source: sqlite3.Connection, target: psycopg.Connection[Any]) -> None:
        rows = source.execute("SELECT * FROM intake_promo ORDER BY id").fetchall()
        counts = {"source": 0, "existing": 0, "migrated": 0, "skipped_unmapped": 0}
        for raw in rows:
            row = dict(raw)
            counts["source"] += 1
            project = self.projects.get(row["customer_id"])
            if project is None:
                counts["skipped_unmapped"] += 1
                continue
            entity, prefix = ENTITY_BY_TABLE["intake_promo"]
            pub = self._pub(prefix, entity, row["id"])
            payload = _parse_json(
                row["payload_json"], table="intake_promo", row_id=row["id"], column="payload_json"
            )
            if not isinstance(payload, dict):
                raise RuntimeError(f"intake_promo#{row['id']} payload_json must decode to object")
            result = target.execute(
                """
                INSERT INTO platform.intake_promo
                  (id,pub_id,tenant_id,project_id,version,created_at,updated_at,kind,payload)
                VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s)
                ON CONFLICT (pub_id) DO NOTHING
                """,
                (
                    self._uuid(entity, row["id"]),
                    pub,
                    project["tenant_id"],
                    project["id"],
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    row["kind"],
                    Jsonb(payload),
                ),
            )
            if result.rowcount:
                counts["migrated"] += 1
            else:
                counts["existing"] += 1
        self.report["intake_promo"] = counts

    def _migrate_trigger_questions(
        self, source: sqlite3.Connection, target: psycopg.Connection[Any]
    ) -> None:
        rows = source.execute("SELECT * FROM intake_trigger_question ORDER BY id").fetchall()
        counts = {"source": 0, "existing": 0, "migrated": 0, "skipped_unmapped": 0}
        for raw in rows:
            row = dict(raw)
            counts["source"] += 1
            project = self.projects.get(row["customer_id"])
            if project is None:
                counts["skipped_unmapped"] += 1
                continue
            entity, prefix = ENTITY_BY_TABLE["intake_trigger_question"]
            pub = self._pub(prefix, entity, row["id"])
            if row["claim_pub_id"] is not None:
                self.dropped["claim_pub_id"] += 1
            # ON CONFLICT DO NOTHING（无目标）：pub_id 冲突与
            # (tenant_id, project_id, text) 唯一约束冲突都按“已存在”对账。
            result = target.execute(
                """
                INSERT INTO platform.intake_trigger_question
                  (id,pub_id,tenant_id,project_id,version,created_at,updated_at,text,status)
                VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    self._uuid(entity, row["id"]),
                    pub,
                    project["tenant_id"],
                    project["id"],
                    parse_time(row["created_at"]),
                    parse_time(row["updated_at"]),
                    row["text"],
                    row["status"],
                ),
            )
            if result.rowcount:
                counts["migrated"] += 1
            else:
                counts["existing"] += 1
        self.report["intake_trigger_question"] = counts

    # ── 入口 ───────────────────────────────────────────────────────────────
    def run(self) -> int:
        source_uri = f"file:{self.source}?mode=ro"
        with (
            sqlite3.connect(source_uri, uri=True) as source,
            psycopg.connect(self.dsn, row_factory=dict_row) as target,
        ):
            source.row_factory = sqlite3.Row
            self._resolve_projects(source, target)
            self._migrate_profile(source, target)
            self._migrate_promo(source, target)
            self._migrate_trigger_questions(source, target)
            if self.apply:
                target.commit()
            else:
                target.rollback()
        self._print_report()
        unmapped = sum(counts["skipped_unmapped"] for counts in self.report.values())
        if self.apply and unmapped and not self.skip_unmapped:
            print(
                f"\nERROR: {unmapped} 行因 customer 未映射被跳过；"
                "确认接受请重跑并加 --skip-unmapped，或用 --project-map 指定落点。",
                file=sys.stderr,
            )
            return 1
        return 0

    def _print_report(self) -> None:
        mode = "APPLY" if self.apply else "DRY-RUN"
        print(f"== legacy intake 迁移对账（{mode}）==")
        print(f"source: {self.source}")
        print(f"snapshot sha256: {self.snapshot_hash}")
        print("\ncustomer → project 归属：")
        for customer_id in sorted(self.projects):
            project = self.projects[customer_id]
            if project is None:
                print(f"  customer {customer_id}: UNMAPPED（V2 无对应客户/项目，相关行跳过）")
            else:
                print(
                    f"  customer {customer_id} → {project['pub_id']} "
                    f"({project['name']}, via {project['via']})"
                )
        print("\n每表对账：")
        for table, counts in self.report.items():
            print(
                f"  {table}: 源行 {counts['source']} / 已存在 {counts['existing']} / "
                f"新迁 {counts['migrated']} / 跳过(未映射) {counts['skipped_unmapped']}"
            )
        print("\n丢弃列计数（V2 无对应物）：")
        for key, value in self.dropped.items():
            print(f"  {key}: {value}")


def _parse_project_map(pairs: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for pair in pairs:
        left, sep, right = pair.partition("=")
        if not sep or not left.strip().isdigit() or not right.strip():
            raise SystemExit(
                f"非法 --project-map 项：{pair!r}（应为 <customer_id>=<project_pub_id>）"
            )
        mapping[int(left.strip())] = right.strip()
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="旧库 sqlite 路径")
    parser.add_argument("--dsn", default=None, help="V2 postgres DSN（缺省取平台配置）")
    parser.add_argument("--apply", action="store_true", help="实际写库（缺省 dry-run）")
    parser.add_argument(
        "--project-map",
        action="append",
        default=[],
        metavar="CUSTOMER_ID=PROJECT_PUB_ID",
        help="把某旧 customer 的 intake 行固定迁到指定 V2 项目（可重复）",
    )
    parser.add_argument(
        "--skip-unmapped",
        action="store_true",
        help="--apply 时允许未映射 customer 的行被跳过（缺省拒绝提交）",
    )
    args = parser.parse_args(argv)
    migrator = IntakeMigrator(
        args.source,
        dsn=args.dsn or _dsn(),
        apply=args.apply,
        project_map=_parse_project_map(args.project_map),
        skip_unmapped=args.skip_unmapped,
    )
    return migrator.run()


if __name__ == "__main__":
    raise SystemExit(main())
