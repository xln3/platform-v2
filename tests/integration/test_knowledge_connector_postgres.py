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
from geo_platform.knowledge.models import ChangeSet, ConnectorRun, KnowledgeObject
from geo_platform.knowledge.repository import KnowledgeRepository
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.models import Tenant
from geo_platform.tenancy.repository import TenantRepository

from domain.siliconindex import project_brand_domain
from domain.siliconindex.snapshot import CORE_FILES, FILES
from tools.run_knowledge_connector_queue import reconcile_snapshot, run_queue

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


def _rewrite_release(root: Path, *, release_id: str, canonical_name: str | None) -> None:
    brands_path = root / "brands.json"
    brands = json.loads(brands_path.read_text(encoding="utf-8"))
    if canonical_name is not None:
        brand = next(value for value in brands if value.get("canonical_name") == "腾讯")
        brand["canonical_name"] = canonical_name
        brands_path.write_text(
            json.dumps(brands, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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
        local_attributes = {**base_entity, "canonical_name": "腾讯（本地测试变更）"}
        local_attributes["analysis_domain"] = "cybersecurity"
        session.add(
            KnowledgeObject(
                pub_id=new_pub_id("kno"),
                tenant_pub_id=tenant_pub_id,
                namespace="shared",
                domain="brand/entity-resolution",
                stable_id=str(base_entity["entity_id"]),
                object_type=str(base_entity["entity_type"]),
                attributes=local_attributes,
                origin="local_review",
                review_status="reviewed",
                visibility="public",
                sync_status="local_ahead",
                version=1,
            )
        )
        session.flush()

        first = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=upstream,
            knowledge_release_dir=tmp_path / "knowledge",
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
            knowledge_release_dir=tmp_path / "knowledge",
            base_upstream_release_id="2026-08-27.4",
        )
        assert duplicate["conflict_change_set_pub_id"] == conflict_id
        assert duplicate["upstream_observations"]["observations_inserted"] == 0
        assert KnowledgeRepository(session, tenant_pub_id).metrics()["conflicts"] == 1

        resolved = reconcile_snapshot(
            session,
            tenant_pub_id=tenant_pub_id,
            snapshot_source=base,
            knowledge_release_dir=tmp_path / "knowledge",
            base_upstream_release_id="2026-08-27.4",
        )
        assert resolved["conflicts"] == []
        conflict = session.query(ChangeSet).filter_by(pub_id=conflict_id).one()
        assert conflict.state == "superseded"
        assert KnowledgeRepository(session, tenant_pub_id).metrics()["conflicts"] == 0
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
