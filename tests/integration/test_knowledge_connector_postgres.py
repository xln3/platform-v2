"""Real-PostgreSQL three-way reconciliation and conflict lifecycle test."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from geo_platform.knowledge.models import ChangeSet, ConnectorRun
from geo_platform.knowledge.repository import KnowledgeRepository
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import Tenant
from geo_platform.tenancy.repository import TenantRepository

from domain.knowledge_evolution.release import KnowledgeReleaseStore
from domain.siliconindex import project_brand_domain
from domain.siliconindex.snapshot import CORE_FILES, FILES
from tools.run_knowledge_connector_queue import reconcile_snapshot, run_queue

pytestmark = pytest.mark.knowledge_postgres

TEST_DATABASE_URL = os.getenv("KNOWLEDGE_POSTGRES_DSN")
POSTGRES_DSN = (TEST_DATABASE_URL or "").replace("postgresql+psycopg://", "postgresql://", 1)
EFFECTIVE_DSN = os.getenv("GEO_POSTGRES_DSN", "").replace(
    "postgresql+psycopg://", "postgresql://", 1
)
PARSED_DSN = urlsplit(POSTGRES_DSN)


@pytest.fixture(scope="module", autouse=True)
def _require_isolated_postgres() -> None:
    if TEST_DATABASE_URL is None:
        pytest.skip("set KNOWLEDGE_POSTGRES_DSN to run the isolated PostgreSQL lane")
    if (
        PARSED_DSN.hostname not in {"127.0.0.1", "localhost"}
        or not PARSED_DSN.path.removeprefix("/").startswith("geo_platform_knowledge_")
        or EFFECTIVE_DSN != POSTGRES_DSN
    ):
        pytest.fail("connector integration requires a matching isolated PostgreSQL database")


def _copy_snapshot(source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    shutil.copy2(source / "manifest.json", target / "manifest.json")
    for name in FILES:
        shutil.copy2(source / f"{name}.json", target / f"{name}.json")
    schema_source = source.parent.parent / "schemas" / "v1"
    if schema_source.is_dir():
        shutil.copytree(schema_source, target / "schemas" / "v1")


def _rewrite_release(
    root: Path,
    *,
    release_id: str,
    canonical_name: str | None,
    eligibility_note: str | None = None,
) -> None:
    brands_path = root / "brands.json"
    brands = json.loads(brands_path.read_text(encoding="utf-8"))
    brand = next(value for value in brands if value.get("brand_id") == "CYB-BR-TENCENT")
    if canonical_name is not None:
        brand["canonical_name"] = canonical_name
    if eligibility_note is not None:
        profile = next(
            value
            for value in brand["comparison_profiles"]
            if value.get("domain") == "cybersecurity"
        )
        profile["eligibility_note"] = eligibility_note
    if canonical_name is not None or eligibility_note is not None:
        brands_path.write_text(
            json.dumps(brands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    digest = hashlib.sha256()
    for name in CORE_FILES:
        digest.update((root / f"{name}.json").read_bytes())
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_id"] = release_id
    manifest["content_hash"] = "sha256:" + digest.hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_reconcile_records_conflict_idempotently_then_supersedes_it(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source = repository_root.parent / "GEO-auto-analysis" / "siliconindex-consumer"
    current_data = source / "public" / "data" / "v1"
    if not (current_data / "manifest.json").is_file():
        pytest.skip("companion SiliconIndex checkout is not available")

    upstream = tmp_path / "upstream"
    base = upstream / "2026-08-27.4"
    _copy_snapshot(current_data, upstream)
    _copy_snapshot(current_data, base)
    _rewrite_release(base, release_id="2026-08-27.4", canonical_name=None)
    _rewrite_release(
        upstream,
        release_id="2026-08-27.5",
        canonical_name="腾讯（上游测试变更）",
    )
    base_entity = next(
        value
        for value in project_brand_domain(base, analysis_domain="cybersecurity")["entities"]
        if value["canonical_name"] == "腾讯"
    )

    with SessionLocal() as session:
        tenant_pub_id = f"tnt_{secrets.token_hex(6)}"
        session.add(Tenant(pub_id=tenant_pub_id, name="Connector integration test"))
        session.flush()
        TenantRepository(session, tenant_pub_id)
        repository = KnowledgeRepository(session, tenant_pub_id)
        knowledge_root = tmp_path / "knowledge"
        store = KnowledgeReleaseStore(knowledge_root)
        manifest = store.publish(
            release_id="knowledge-connector-test",
            schema_version="knowledge-release-v1",
            documents={
                "brand/entity-resolution": {
                    "source_release_id": "2026-08-27.4",
                    "analysis_domains": {"cybersecurity": {"entities": []}},
                }
            },
            parent_release_id=None,
            quality_report={"quality_gate": "test_fixture"},
            activate=True,
        )
        release = repository.add_release(
            namespace="shared",
            domain="brand/entity-resolution",
            release_id="knowledge-connector-test",
            parent_release_id=None,
            schema_version="knowledge-release-v1",
            content_hash=str(manifest["content_hash"]),
            artifact_uri=str(knowledge_root / "knowledge-connector-test"),
            quality_report={"quality_gate": "test_fixture"},
            actor="test:publisher",
        )
        base_projection = project_brand_domain(base, analysis_domain="cybersecurity")
        changes = []
        for entity in base_projection["entities"]:
            if entity.get("review_status") != "reviewed":
                continue
            attributes = dict(entity)
            if entity["entity_id"] == base_entity["entity_id"]:
                attributes["canonical_name"] = "腾讯（本地测试变更）"
            attributes["analysis_domain"] = "cybersecurity"
            changes.append(
                {
                    "kind": "knowledge_object",
                    "operation": "upsert",
                    "stable_id": entity["entity_id"],
                    "object_type": entity["entity_type"],
                    "attributes": attributes,
                    "origin": "local_review",
                    "review_status": "reviewed",
                    "visibility": "public",
                    "sync_status": (
                        "local_ahead"
                        if entity["entity_id"] == base_entity["entity_id"]
                        else "reconciled"
                    ),
                }
            )
        repository.materialize_changes(
            namespace="shared",
            domain="brand/entity-resolution",
            changes=changes,
            release=release,
            base_release_id=None,
        )
        repository.activate_release(
            namespace="shared",
            domain="brand/entity-resolution",
            release_id=release.release_id,
            previous_release_id=None,
            action="activate",
            actor="test:publisher",
        )
        session.commit()

        first = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=upstream,
            knowledge_release_dir=knowledge_root,
            base_upstream_release_id="2026-08-27.4",
        )
        assert first["can_prepare_merge"] is False
        assert first["upstream_changed_ids"] == [base_entity["entity_id"]]
        assert first["local_changed_ids"] == [base_entity["entity_id"]]
        assert [value["path"] for value in first["conflicts"]] == [
            f"/{base_entity['entity_id']}/canonical_name"
        ]
        assert first["upstream_observations"] == {
            "changed": 1,
            "observations_inserted": 1,
            "reopened": 0,
        }
        conflict_id = first["conflict_change_set_pub_id"]

        duplicate = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=upstream,
            knowledge_release_dir=knowledge_root,
            base_upstream_release_id="2026-08-27.4",
        )
        assert duplicate["conflict_change_set_pub_id"] == conflict_id
        assert duplicate["upstream_observations"]["observations_inserted"] == 0
        assert KnowledgeRepository(session, tenant_pub_id).metrics()["conflicts"] == 1

        resolved = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=base,
            knowledge_release_dir=knowledge_root,
            base_upstream_release_id="2026-08-27.4",
        )
        assert resolved["conflicts"] == []
        conflict = session.query(ChangeSet).filter_by(pub_id=conflict_id).one()
        assert conflict.state == "superseded"
        assert KnowledgeRepository(session, tenant_pub_id).metrics()["conflicts"] == 0

        _rewrite_release(
            upstream,
            release_id="2026-08-27.5",
            canonical_name="腾讯",
            eligibility_note="上游新增但不与本地名称修改冲突的说明。",
        )
        disjoint = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=upstream,
            knowledge_release_dir=knowledge_root,
            base_upstream_release_id="2026-08-27.4",
        )
        assert disjoint["conflicts"] == []
        assert disjoint["can_prepare_merge"] is True
        assert disjoint["local_changed_ids"] == [base_entity["entity_id"]]
        export = json.loads(Path(disjoint["local_export"]["artifact"]).read_text(encoding="utf-8"))
        assert export["base_upstream_release_id"] == "2026-08-27.5"
        assert export["changes"][0]["attributes"]["canonical_name"] == ("腾讯（本地测试变更）")
        assert export["changes"][0]["attributes"]["eligibility_note"] == (
            "上游新增但不与本地名称修改冲突的说明。"
        )
        session.rollback()


def test_connector_queue_records_success_and_fail_loud_receipts(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    snapshot = (
        repository_root.parent
        / "GEO-auto-analysis"
        / "siliconindex-consumer"
        / "public"
        / "data"
        / "v1"
    )
    if not (snapshot / "manifest.json").is_file():
        pytest.skip("companion SiliconIndex checkout is not available")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    tenant_pub_id = f"tnt_{secrets.token_hex(6)}"

    with SessionLocal() as session:
        session.add(Tenant(pub_id=tenant_pub_id, name="Connector queue integration test"))
        session.flush()
        TenantRepository(session, tenant_pub_id)
        success = ConnectorRun(
            pub_id=new_pub_id("kcr"),
            tenant_pub_id=tenant_pub_id,
            namespace="shared",
            domain="brand/entity-resolution",
            adapter="siliconindex-static",
            operation="import",
            status="queued",
            cursor={},
            result={},
        )
        session.add(success)
        session.commit()
        success_pub_id = success.pub_id

    summary = run_queue(
        tenant_pub_id=tenant_pub_id,
        snapshot_source=snapshot,
        knowledge_release_dir=tmp_path / "knowledge",
        limit=1,
    )
    assert summary == {"claimed": 1, "completed": 1, "conflicts": 0, "failed": 0}
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        completed = session.query(ConnectorRun).filter_by(pub_id=success_pub_id).one()
        assert completed.status == "success"
        assert completed.upstream_release_id == manifest["release_id"]
        assert completed.result["content_hash"] == manifest["content_hash"]
        failed = ConnectorRun(
            pub_id=new_pub_id("kcr"),
            tenant_pub_id=tenant_pub_id,
            namespace="shared",
            domain="brand/entity-resolution",
            adapter="siliconindex-static",
            operation="publish",
            status="queued",
            upstream_release_id=manifest["release_id"],
            cursor={
                "expected_release_id": manifest["release_id"],
                "expected_content_hash": "sha256:" + "0" * 64,
            },
            result={},
        )
        session.add(failed)
        session.commit()
        failed_pub_id = failed.pub_id

    summary = run_queue(
        tenant_pub_id=tenant_pub_id,
        snapshot_source=snapshot,
        knowledge_release_dir=tmp_path / "knowledge",
        limit=1,
    )
    assert summary == {"claimed": 1, "completed": 0, "conflicts": 0, "failed": 1}
    with SessionLocal() as session:
        TenantRepository(session, tenant_pub_id)
        receipt = session.query(ConnectorRun).filter_by(pub_id=failed_pub_id).one()
        assert receipt.status == "failed"
        assert receipt.error_code == "published_content_hash_mismatch"
        assert receipt.finished_at is not None
