"""Real-PostgreSQL governance closure, RLS, audit, and immutable release test."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.knowledge import router as knowledge_router_module
from geo_platform.knowledge.models import ChangeSet
from geo_platform.knowledge.repository import KnowledgeRepository
from geo_platform.main import app
from geo_platform.tenancy.database import SessionLocal
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.repository import TenantRepository

from domain.knowledge_evolution.release import KnowledgeReleaseStore

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
        pytest.fail(
            "knowledge integration requires matching GEO_POSTGRES_DSN and a loopback "
            "geo_platform_knowledge_* database"
        )


def _headers(tenant: str, subject: str, role: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
    }


def _bootstrap(client: TestClient, marker: str) -> tuple[str, dict[str, str]]:
    subject = f"knowledge-admin-{marker}"
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    tenant = str(response.json()["tenant_pub_id"])
    return tenant, _headers(tenant, subject, "admin")


def _member(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    role: str,
    marker: str,
) -> dict[str, str]:
    subject = f"knowledge-{role}-{marker}"
    response = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": subject, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return _headers(admin_headers["X-Tenant-Id"], subject, role)


def test_release_membership_rollback_switches_database_read_state(
    tmp_path: Path,
) -> None:
    marker = secrets.token_hex(6)
    with TestClient(app) as client:
        tenant, _admin = _bootstrap(client, marker)
    with SessionLocal() as session:
        TenantRepository(session, tenant)
        repository = KnowledgeRepository(session, tenant)
        first = repository.add_release(
            namespace="shared",
            domain="source/type-fixture",
            release_id=f"membership-first-{marker}",
            parent_release_id=None,
            schema_version="1",
            content_hash="sha256:" + "1" * 64,
            artifact_uri=str(tmp_path / "first"),
            quality_report={},
            actor="publisher-a",
        )
        repository.materialize_changes(
            namespace="shared",
            domain="source/type-fixture",
            changes=(
                {
                    "kind": "knowledge_object",
                    "operation": "upsert",
                    "stable_id": "source-type:forum",
                    "object_type": "source_type",
                    "attributes": {"key": "forum", "source_type": "social_source-v1"},
                },
            ),
            release=first,
            base_release_id=None,
        )
        repository.activate_release(
            namespace="shared",
            domain="source/type-fixture",
            release_id=first.release_id,
            previous_release_id=None,
            action="activate",
            actor="publisher-a",
        )
        session.commit()

        second = repository.add_release(
            namespace="shared",
            domain="source/type-fixture",
            release_id=f"membership-second-{marker}",
            parent_release_id=first.release_id,
            schema_version="1",
            content_hash="sha256:" + "2" * 64,
            artifact_uri=str(tmp_path / "second"),
            quality_report={},
            actor="publisher-b",
        )
        repository.materialize_changes(
            namespace="shared",
            domain="source/type-fixture",
            changes=(
                {
                    "kind": "knowledge_object",
                    "operation": "upsert",
                    "stable_id": "source-type:forum",
                    "object_type": "source_type",
                    "attributes": {"key": "forum", "source_type": "social_source-v2"},
                },
            ),
            release=second,
            base_release_id=first.release_id,
        )
        repository.activate_release(
            namespace="shared",
            domain="source/type-fixture",
            release_id=second.release_id,
            previous_release_id=first.release_id,
            action="activate",
            actor="publisher-b",
        )
        session.commit()
        assert (
            repository.current_objects(namespace="shared", domain="source/type-fixture")[
                0
            ].attributes["source_type"]
            == "social_source-v2"
        )

        repository.activate_release(
            namespace="shared",
            domain="source/type-fixture",
            release_id=first.release_id,
            previous_release_id=second.release_id,
            action="rollback",
            actor="publisher-c",
        )
        session.commit()
        rolled_back = repository.current_objects(namespace="shared", domain="source/type-fixture")
        assert rolled_back[0].attributes["source_type"] == "social_source-v1"
        assert (
            repository.current_objects(
                namespace="shared",
                domain="source/type-fixture",
                release_id=second.release_id,
            )[0].attributes["source_type"]
            == "social_source-v2"
        )


def test_observation_to_release_closure_rbac_rls_and_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings().model_copy(
        update={
            "knowledge_release_dir": str(tmp_path / "releases"),
            "knowledge_llm_api_key": "",
            "research_llm_api_key": "",
        }
    )
    monkeypatch.setattr(knowledge_router_module, "get_settings", lambda: settings)
    marker = secrets.token_hex(6)
    with TestClient(app) as client:
        tenant, admin = _bootstrap(client, marker)
        operator = _member(client, admin, role="operator", marker=marker)
        reviewer_one = _member(client, admin, role="reviewer", marker=f"one-{marker}")
        reviewer_two = _member(client, admin, role="reviewer", marker=f"two-{marker}")

        observation = {
            "namespace": "integration",
            "domain": "source/type-fixture",
            "task": "classify",
            "surface_form": "forum",
            "normalized_key": "forum",
            "source_type": "integration-test",
            "source_ref_hash": "sha256:" + "1" * 64,
            "idempotency_key": "knowledge-integration-" + marker,
            "safe_context": None,
            "data_classification": "internal",
            "visibility": "private",
            "payload": {"policy_version": "fixture-v1"},
        }
        first = client.post(
            "/api/v2/knowledge/v1/observations:ingest",
            headers=operator,
            json={"observations": [observation]},
        )
        duplicate = client.post(
            "/api/v2/knowledge/v1/observations:ingest",
            headers=operator,
            json={"observations": [observation]},
        )
        assert first.status_code == 202 and first.json()["accepted"] == 1
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] == 1

        candidates = client.get(
            "/api/v2/knowledge/v1/candidates",
            headers=operator,
            params={"namespace": "integration", "domain": "source/type-fixture"},
        )
        assert candidates.status_code == 200, candidates.text
        candidate_pub_id = str(candidates.json()["data"][0]["pub_id"])

        proposal_response = client.post(
            "/api/v2/knowledge/v1/proposals",
            headers=operator,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "candidate_pub_id": candidate_pub_id,
                "operation": "create",
                "target_stable_id": "source-type:forum",
                "payload": {"key": "forum", "source_type": "social_source"},
                "policy_version": "fixture-v1",
            },
        )
        assert proposal_response.status_code == 201, proposal_response.text
        proposal_pub_id = str(proposal_response.json()["pub_id"])

        forbidden_review = client.post(
            f"/api/v2/knowledge/v1/proposals/{proposal_pub_id}/adjudications",
            headers=operator,
            json={
                "decision": "approved",
                "reason": "Operator must not be allowed to self-review this proposal.",
                "policy_version": "fixture-v1",
            },
        )
        assert forbidden_review.status_code == 403

        evidence = client.post(
            "/api/v2/knowledge/v1/evidence",
            headers=operator,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "proposal_pub_id": proposal_pub_id,
                "source_uri": "https://example.test/source-types",
                "content_hash": "sha256:" + "2" * 64,
                "publisher": "Example Standards Body",
                "claim": "Forum is a social source type.",
                "stance": "supports",
                "summary": "Authoritative fixture evidence.",
                "trust_tier": "authoritative",
                "visibility": "public",
                "data_classification": "public",
                "acquired_at": datetime.now(UTC).isoformat(),
            },
        )
        assert evidence.status_code == 201, evidence.text

        adjudication = client.post(
            f"/api/v2/knowledge/v1/proposals/{proposal_pub_id}/adjudications",
            headers=reviewer_one,
            json={
                "decision": "approved",
                "reason": "The authoritative fixture evidence supports this classification.",
                "policy_version": "fixture-v1",
                "after_value": {"source_type": "social_source"},
            },
        )
        assert adjudication.status_code == 201, adjudication.text

        substituted_change = client.post(
            "/api/v2/knowledge/v1/change-sets",
            headers=reviewer_one,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "changes": [
                    {
                        "kind": "knowledge_object",
                        "operation": "upsert",
                        "proposal_pub_id": proposal_pub_id,
                        "stable_id": "source-type:forum",
                        "object_type": "source_type",
                        "attributes": {"key": "forum", "source_type": "official_source"},
                        "review_status": "reviewed",
                        "visibility": "public",
                        "evidence_pub_ids": [evidence.json()["pub_id"]],
                    }
                ],
                "visibility": "public",
            },
        )
        assert substituted_change.status_code == 409
        assert substituted_change.json()["error"]["code"] == "change_content_proposal_mismatch"

        change_set_response = client.post(
            "/api/v2/knowledge/v1/change-sets",
            headers=reviewer_one,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "changes": [
                    {
                        "kind": "knowledge_object",
                        "operation": "upsert",
                        "proposal_pub_id": proposal_pub_id,
                        "stable_id": "source-type:forum",
                        "object_type": "source_type",
                        "attributes": {"key": "forum", "source_type": "social_source"},
                        "review_status": "reviewed",
                        "visibility": "public",
                        "evidence_pub_ids": [evidence.json()["pub_id"]],
                    }
                ],
                "visibility": "public",
            },
        )
        assert change_set_response.status_code == 201, change_set_response.text
        change_set_pub_id = str(change_set_response.json()["pub_id"])

        self_approval = client.post(
            f"/api/v2/knowledge/v1/change-sets/{change_set_pub_id}/approve",
            headers=reviewer_one,
        )
        assert self_approval.status_code == 409
        approval = client.post(
            f"/api/v2/knowledge/v1/change-sets/{change_set_pub_id}/approve",
            headers=reviewer_two,
        )
        assert approval.status_code == 200, approval.text

        release_id = f"fixture-{marker}"
        release = client.post(
            "/api/v2/knowledge/v1/releases",
            headers=admin,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "release_id": release_id,
                "schema_version": "source-type-v1",
                "change_set_pub_ids": [change_set_pub_id],
                "quality_report": {"fixture_contract": "passed"},
                "activate": True,
            },
        )
        assert release.status_code == 201, release.text
        assert release.json()["release_id"] == release_id
        assert (tmp_path / "releases" / "CURRENT").read_text(encoding="utf-8") == release_id

        reread = client.post(
            "/api/v2/knowledge/v1/runtime/resolve",
            headers=operator,
            json={
                "request_id": f"source-type-reread-{marker}",
                "namespace": "integration",
                "domain": "source/type-fixture",
                "task": "classify",
                "items": [{"id": "forum", "value": "forum"}],
                "context": {},
                "policy": "deterministic_only",
                "policy_id": "fixture-contract",
                "policy_version": "2",
                "expected_release_id": release_id,
            },
        )
        assert reread.status_code == 200, reread.text
        assert reread.json()["release"]["release_id"] == release_id
        assert reread.json()["decisions"][0]["value"] == {"source_type": "social_source"}
        assert reread.json()["decisions"][0]["knowledge_status"] == "reviewed_local"

        # A database membership that does not project to the immutable artifact
        # must be rejected before either activation pointer moves.
        malformed_release_id = f"fixture-malformed-{marker}"
        store = KnowledgeReleaseStore(tmp_path / "releases")
        current_documents, _current_manifest = store.load_documents(release_id)
        malformed_manifest = store.publish(
            release_id=malformed_release_id,
            schema_version="source-type-v1",
            documents=current_documents,
            parent_release_id=release_id,
            quality_report={"fixture_contract": "deliberate_materialization_mismatch"},
            activate=False,
        )
        with SessionLocal() as direct_session:
            TenantRepository(direct_session, tenant)
            direct_repository = KnowledgeRepository(direct_session, tenant)
            malformed_release = direct_repository.add_release(
                namespace="integration",
                domain="source/type-fixture",
                release_id=malformed_release_id,
                parent_release_id=release_id,
                schema_version="source-type-v1",
                content_hash=str(malformed_manifest["content_hash"]),
                artifact_uri=str(tmp_path / "releases" / malformed_release_id),
                quality_report={"fixture_contract": "deliberate_materialization_mismatch"},
                actor="integration:malformed-publisher",
            )
            direct_repository.materialize_changes(
                namespace="integration",
                domain="source/type-fixture",
                changes=(
                    {
                        "kind": "knowledge_object",
                        "operation": "upsert",
                        "stable_id": "source-type:forum",
                        "object_type": "source_type",
                        "attributes": {"key": "forum", "source_type": "editorial_source"},
                        "review_status": "reviewed",
                        "visibility": "public",
                    },
                ),
                release=malformed_release,
                base_release_id=release_id,
            )
            direct_session.commit()
        malformed_activation = client.post(
            f"/api/v2/knowledge/v1/releases/{malformed_release_id}/activate",
            headers=admin,
            json={"namespace": "integration", "domain": "source/type-fixture"},
        )
        assert malformed_activation.status_code == 409, malformed_activation.text
        assert malformed_activation.json()["error"]["code"] == ("release_materialization_mismatch")
        assert store.current_release_id() == release_id
        with SessionLocal() as direct_session:
            TenantRepository(direct_session, tenant)
            assert (
                KnowledgeRepository(direct_session, tenant).active_release_id(
                    namespace="integration",
                    domain="source/type-fixture",
                )
                == release_id
            )

        upstream_reopen = client.post(
            f"/api/v2/knowledge/v1/candidates/{candidate_pub_id}/reopen",
            headers=reviewer_one,
            json={
                "reason": "A new external release changed the evidence for this object.",
                "evidence_version": "fixture-upstream-v2",
            },
        )
        assert upstream_reopen.status_code == 200, upstream_reopen.text
        assert upstream_reopen.json()["state"] == "aggregated"
        duplicate_upstream_reopen = client.post(
            f"/api/v2/knowledge/v1/candidates/{candidate_pub_id}/reopen",
            headers=reviewer_one,
            json={
                "reason": "The same weekly evidence must not reopen it again.",
                "evidence_version": "fixture-upstream-v2",
            },
        )
        assert duplicate_upstream_reopen.status_code == 409
        assert duplicate_upstream_reopen.json()["error"]["code"] == "candidate_not_reopenable"

        metrics = client.get("/api/v2/knowledge/v1/metrics", headers=reviewer_one)
        audits = client.get(
            "/api/v2/knowledge/v1/audit-events",
            headers=reviewer_one,
            params={"namespace": "integration", "domain": "source/type-fixture"},
        )
        events = client.get(
            "/api/v2/knowledge/v1/events",
            headers=reviewer_one,
            params={"namespace": "integration", "domain": "source/type-fixture"},
        )
        assert metrics.status_code == 200 and metrics.json()["observations"] == 1
        assert metrics.json()["active_release_id"] == release_id
        assert audits.status_code == 200
        assert {row["action"] for row in audits.json()} >= {
            "proposal.created",
            "proposal.approved",
            "change_set.approved",
            "release.published",
            "release.activate",
        }
        assert events.status_code == 200
        assert all(row["schema_version"] == "knowledge-event-v1" for row in events.json())
        assert all(row["payload_hash"].startswith("sha256:") for row in events.json())
        assert [row["occurred_at"] for row in events.json()] == sorted(
            row["occurred_at"] for row in events.json()
        )

        disputed_observation = {
            **observation,
            "surface_form": "disputed-forum",
            "normalized_key": "disputed-forum",
            "idempotency_key": "knowledge-disputed-" + marker,
        }
        disputed_ingest = client.post(
            "/api/v2/knowledge/v1/observations:ingest",
            headers=operator,
            json={"observations": [disputed_observation]},
        )
        assert disputed_ingest.status_code == 202, disputed_ingest.text
        disputed_candidates = client.get(
            "/api/v2/knowledge/v1/candidates",
            headers=operator,
            params={"namespace": "integration", "domain": "source/type-fixture"},
        )
        disputed_candidate = next(
            row
            for row in disputed_candidates.json()["data"]
            if "disputed-forum" in row["surface_forms"]
        )
        disputed_candidate_pub_id = str(disputed_candidate["pub_id"])
        disputed_proposal_response = client.post(
            "/api/v2/knowledge/v1/proposals",
            headers=operator,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "candidate_pub_id": disputed_candidate_pub_id,
                "operation": "create",
                "target_stable_id": "source-type:disputed-forum",
                "payload": {"key": "disputed-forum", "source_type": "social_source"},
                "policy_version": "fixture-v1",
            },
        )
        assert disputed_proposal_response.status_code == 201, disputed_proposal_response.text
        disputed_proposal_pub_id = str(disputed_proposal_response.json()["pub_id"])
        evidence_base = {
            "namespace": "integration",
            "domain": "source/type-fixture",
            "proposal_pub_id": disputed_proposal_pub_id,
            "publisher": "Example Standards Body",
            "claim": "The disputed fixture classification has conflicting primary sources.",
            "summary": "Deliberate integration-test contradiction.",
            "trust_tier": "primary",
            "visibility": "public",
            "data_classification": "public",
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        supporting = client.post(
            "/api/v2/knowledge/v1/evidence",
            headers=operator,
            json={
                **evidence_base,
                "source_uri": "https://example.test/disputed-support",
                "content_hash": "sha256:" + "3" * 64,
                "stance": "supports",
            },
        )
        opposing = client.post(
            "/api/v2/knowledge/v1/evidence",
            headers=operator,
            json={
                **evidence_base,
                "source_uri": "https://example.test/disputed-opposition",
                "content_hash": "sha256:" + "4" * 64,
                "stance": "opposes",
            },
        )
        assert supporting.status_code == 201, supporting.text
        assert opposing.status_code == 201, opposing.text

        contradictory_approval = client.post(
            f"/api/v2/knowledge/v1/proposals/{disputed_proposal_pub_id}/adjudications",
            headers=reviewer_one,
            json={
                "decision": "approved",
                "reason": "This must remain blocked until the primary-source conflict is resolved.",
                "policy_version": "fixture-v1",
            },
        )
        assert contradictory_approval.status_code == 409
        assert (
            contradictory_approval.json()["error"]["code"]
            == "contradictory_authoritative_evidence_requires_resolution"
        )
        deferred = client.post(
            f"/api/v2/knowledge/v1/proposals/{disputed_proposal_pub_id}/adjudications",
            headers=reviewer_one,
            json={
                "decision": "deferred",
                "reason": "The contradictory primary evidence needs domain-owner resolution.",
                "policy_version": "fixture-v1",
            },
        )
        assert deferred.status_code == 201, deferred.text

        terminal_evidence = client.post(
            "/api/v2/knowledge/v1/evidence",
            headers=operator,
            json={
                **evidence_base,
                "source_uri": "https://example.test/disputed-later",
                "content_hash": "sha256:" + "5" * 64,
                "stance": "neutral",
            },
        )
        assert terminal_evidence.status_code == 409, terminal_evidence.text
        assert (
            terminal_evidence.json()["error"]["code"]
            == "terminal_proposal_evidence_requires_candidate_reopen"
        )
        terminal_readjudication = client.post(
            f"/api/v2/knowledge/v1/proposals/{disputed_proposal_pub_id}/adjudications",
            headers=reviewer_two,
            json={
                "decision": "rejected",
                "reason": "A terminal proposal cannot be overwritten by another adjudication.",
                "policy_version": "fixture-v1",
            },
        )
        assert terminal_readjudication.status_code == 409
        assert terminal_readjudication.json()["error"]["code"] == "proposal_already_adjudicated"

        proposal_without_reopen = client.post(
            "/api/v2/knowledge/v1/proposals",
            headers=operator,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "candidate_pub_id": disputed_candidate_pub_id,
                "operation": "update",
                "target_stable_id": "source-type:disputed-forum",
                "payload": {"key": "disputed-forum", "source_type": "social_source"},
                "policy_version": "fixture-v1",
            },
        )
        assert proposal_without_reopen.status_code == 409
        no_trigger_reopen = client.post(
            f"/api/v2/knowledge/v1/candidates/{disputed_candidate_pub_id}/reopen",
            headers=reviewer_two,
            json={"reason": "A weekly retry without a valid trigger must stay closed."},
        )
        assert no_trigger_reopen.status_code == 409
        manual_reopen = client.post(
            f"/api/v2/knowledge/v1/candidates/{disputed_candidate_pub_id}/reopen",
            headers=reviewer_two,
            json={
                "reason": "A domain reviewer explicitly authorizes a fresh adjudication cycle.",
                "manual_override": True,
            },
        )
        assert manual_reopen.status_code == 200, manual_reopen.text
        assert manual_reopen.json()["state"] == "aggregated"

        revised_proposal = client.post(
            "/api/v2/knowledge/v1/proposals",
            headers=operator,
            json={
                "namespace": "integration",
                "domain": "source/type-fixture",
                "candidate_pub_id": disputed_candidate_pub_id,
                "operation": "update",
                "target_stable_id": "source-type:disputed-forum",
                "payload": {"key": "disputed-forum", "source_type": "social_source"},
                "policy_version": "fixture-v1",
            },
        )
        assert revised_proposal.status_code == 201, revised_proposal.text
        revised_proposal_pub_id = str(revised_proposal.json()["pub_id"])
        revised_deferred = client.post(
            f"/api/v2/knowledge/v1/proposals/{revised_proposal_pub_id}/adjudications",
            headers=reviewer_one,
            json={
                "decision": "deferred",
                "reason": "Wait for the next evidence-policy revision before reviewing again.",
                "policy_version": "fixture-v1",
            },
        )
        assert revised_deferred.status_code == 201, revised_deferred.text
        policy_reopen = client.post(
            f"/api/v2/knowledge/v1/candidates/{disputed_candidate_pub_id}/reopen",
            headers=reviewer_two,
            json={
                "reason": "The review policy changed and requires a fresh adjudication cycle.",
                "policy_version": "fixture-v2",
            },
        )
        assert policy_reopen.status_code == 200, policy_reopen.text
        assert policy_reopen.json()["policy_version"] == "fixture-v2"

        other_tenant, other_admin = _bootstrap(client, f"other-{marker}")
        assert other_tenant != tenant
        isolated = client.get(
            "/api/v2/knowledge/v1/candidates",
            headers=other_admin,
            params={"namespace": "integration", "domain": "source/type-fixture"},
        )
        assert isolated.status_code == 200 and isolated.json()["total"] == 0

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant,))
        pub_id = connection.execute(
            "SELECT pub_id FROM knowledge.observation WHERE tenant_pub_id=%s LIMIT 1",
            (tenant,),
        ).fetchone()
        assert pub_id is not None
        with pytest.raises(psycopg.errors.RaiseException, match="append_only_table"):
            connection.execute(
                "UPDATE knowledge.observation SET state='changed' WHERE pub_id=%s",
                (pub_id[0],),
            )

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant,))
        object_pub_id = connection.execute(
            "SELECT pub_id FROM knowledge.knowledge_object WHERE tenant_pub_id=%s LIMIT 1",
            (tenant,),
        ).fetchone()
        assert object_pub_id is not None
        with pytest.raises(psycopg.errors.RaiseException, match="append_only_table"):
            connection.execute(
                "UPDATE knowledge.knowledge_object SET sync_status='changed' WHERE pub_id=%s",
                (object_pub_id[0],),
            )

    artifact = tmp_path / "releases" / release_id / "manifest.json"
    assert hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_metrics_only_count_unresolved_connector_conflicts() -> None:
    tenant = f"tnt_metrics_{secrets.token_hex(5)}"
    common = {
        "tenant_pub_id": tenant,
        "namespace": "shared",
        "domain": "brand/entity-resolution",
        "base_release_id": "2026-08-26.3",
        "changes": [],
        "dependency_ids": [],
        "conflicts": [{"path": "/brands/brand:test", "base": {}, "upstream": {}, "local": {}}],
        "visibility": "public",
        "created_by": "system:siliconindex-connector",
    }
    with SessionLocal() as session:
        resolved = ChangeSet(pub_id=new_pub_id("kcs"), state="superseded", **common)
        open_conflict = ChangeSet(pub_id=new_pub_id("kcs"), state="conflict", **common)
        session.add_all((resolved, open_conflict))
        session.flush()
        repository = KnowledgeRepository(session, tenant)
        assert repository.metrics()["conflicts"] == 1
        open_conflict.state = "superseded"
        session.flush()
        assert repository.metrics()["conflicts"] == 0
        session.rollback()
