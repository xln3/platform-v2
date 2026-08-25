"""Real-PostgreSQL submission-v2 restricted-entry vertical slice.

The suite is intentionally opt-in and refuses every non-loopback DSN.  It
creates only durable database fixtures; no provider, browser, object-store, or
event-bus I/O is performed.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from typing import cast
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg
import pytest
from geo_platform.collection.identity_v2 import (
    CampaignAssemblyBlueprint,
    CampaignFreezeRequest,
    ConfigFreezeRequest,
    FrozenConfigRevision,
    freeze_campaign,
    freeze_config,
)
from geo_platform.collection.quota_v2 import (
    ConnectionProtocol,
    ReserveQuotaRequest,
    reserve_quota_after_operation_admission,
)
from geo_platform.collection.submission_repository_v2 import (
    S10_FUNCTION_CONTRACTS,
    PostgresSubmissionRepository,
    RepositoryConnection,
    RepositoryScope,
)
from geo_platform.collection.submission_v2 import (
    CaptureAdmissionDecision,
    PreparedSubmissionRef,
    ResolvedSubmissionContext,
    SlotOutcomeFact,
    SubmissionWorkItem,
)

from domain.collection.submission import (
    CaptureChannel,
    CaptureDataClassification,
    CaptureDisposition,
    CaptureNormalizationDecision,
    CaptureProvenance,
    CaptureStagingRef,
    LeaseFenceRef,
    OperationIdentity,
    OperationKeyMaterial,
    OperationRef,
    OutboxEventRef,
    OwnerAuthorityRef,
    PrepareSubmissionCommand,
    QuotaTerminalEffect,
    RequestManifest,
    SlotOutcome,
    SubmissionOperationTruth,
    SurfaceProductRef,
    TerminalReason,
    TerminalSubmissionTransition,
    TerminalSubmissionTruth,
    WorkflowOperationInput,
    authority_digest,
    canonical_json,
    derive_slot_outcome,
    deterministic_operation_key,
    deterministic_outbox_key,
    deterministic_provider_idempotency_key,
    lease_fence_set_digest,
    link_immutable_capture,
    normalize_capture,
    operation_ref,
    request_manifest_digest,
)
from domain.collection.surface import CaptureState, CollectionSurface, SendState

from . import test_collection_quota_v2 as quota_integration
from .test_collection_quota_v2 import _seed_service_fixture

PREPARE_SIGNATURE = (
    "platform.prepare_collection_submission_request_v2("
    "uuid,uuid,uuid,integer,text,text,text,text,text,text,text,timestamptz)"
)
CREATE_OPERATION_SIGNATURE = (
    "platform.create_collection_submission_operation_v2("
    "uuid,uuid,text,integer,text,text,timestamptz,text,text,text,text,text,text,text,text)"
)
CLAIM_SIGNATURE = (
    "platform.claim_collection_submission_v2("
    "uuid,uuid,uuid,text,integer,uuid,integer,text,text,text,text,text,text,text,text,text,"
    "timestamptz)"
)
RECONCILIATION_READY_SIGNATURE = (
    "platform.mark_collection_dispatch_reconciliation_ready_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,timestamptz)"
)
RESTRICTED_SIGNATURES = (
    CREATE_OPERATION_SIGNATURE,
    PREPARE_SIGNATURE,
    CLAIM_SIGNATURE,
    RECONCILIATION_READY_SIGNATURE,
)

CREATE_OPERATION_SQL = """
SELECT operation_id, created
FROM platform.create_collection_submission_operation_v2(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
"""

PREPARE_SQL = """
SELECT request_manifest_id, capture_truth_id, prepared
FROM platform.prepare_collection_submission_request_v2(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
"""
CLAIM_SQL = """
SELECT dispatch_id, persisted_claim_pub_id, claim_acquired
FROM platform.claim_collection_submission_v2(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
"""
MARK_RECONCILIATION_READY_SQL = """
SELECT platform.mark_collection_dispatch_reconciliation_ready_v2(
  %s,%s,%s,%s,%s,%s,%s,%s
)
"""


@dataclass(frozen=True, slots=True)
class _ResourceLease:
    registration_id: UUID
    resource_pub_id: str
    resource_kind: str
    resource_role: str
    mapping_revision: str
    capacity_id: UUID
    lease_id: UUID
    lease_pub_id: str
    acquired_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _SubmissionFixture:
    dsn: str
    tenant_id: UUID
    project_id: UUID
    operation_id: UUID
    operation_pub_id: str
    identity: OperationIdentity
    operation_ref: OperationRef
    surface_product: SurfaceProductRef
    binding_id: UUID
    binding_pub_id: str
    owner_handle: str
    execution_grant_id: UUID
    execution_grant_pub_id: str
    grant_hash: str
    fence_set_hash: str
    authority: OwnerAuthorityRef
    reservation_pub_id: str
    leases: tuple[_ResourceLease, ...]
    create_operation_args: tuple[object, ...]
    prepare_args: tuple[object, ...]
    prepared_manifest_id: UUID
    capture_truth_id: UUID
    claim_args: tuple[object, ...]


def _test_dsn() -> str:
    dsn = os.getenv("COLLECTION_SUBMISSION_V2_TEST_DSN")
    if not dsn:
        pytest.skip("isolated submission-v2 PostgreSQL DSN not configured")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"}:
        pytest.skip("submission-v2 integration tests require a PostgreSQL DSN")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("submission-v2 integration tests refuse non-loopback PostgreSQL")
    if not parsed.path or parsed.path == "/":
        pytest.skip("submission-v2 integration tests require an explicit database")
    return dsn


def _pub(prefix: str, value: UUID) -> str:
    return f"{prefix}_{value.hex[:26]}"


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _set_scope(
    connection: psycopg.Connection[tuple[object, ...]],
    tenant_id: UUID,
    *,
    role: str | None = None,
) -> None:
    connection.execute("SET LOCAL TIME ZONE 'UTC'")
    if role is not None:
        if role not in {"geo_api", "geo_worker"}:
            raise AssertionError("integration role is not allow-listed")
        connection.execute(f"SET LOCAL ROLE {role}")
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))


def _ensure_s10(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    table = connection.execute(
        "SELECT to_regclass('platform.collection_submission_request_manifest_v2')"
    ).fetchone()
    if table is None or table[0] is None:
        pytest.skip("s10 collection submission migration is not installed")
    missing = tuple(
        signature
        for signature in RESTRICTED_SIGNATURES
        if connection.execute("SELECT to_regprocedure(%s)", (signature,)).fetchone() == (None,)
    )
    if missing:
        pytest.skip(f"s10 restricted signatures are not installed: {missing!r}")
    roles = connection.execute(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('geo_api','geo_worker') ORDER BY rolname"
    ).fetchall()
    if tuple(str(row[0]) for row in roles) != ("geo_api", "geo_worker"):
        pytest.skip("s10 runtime roles are not installed")


def _clone_binding_with_single_owner(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    tenant_id: UUID,
    project_id: UUID,
    source_binding_id: UUID,
    token: str,
    now: datetime,
) -> tuple[UUID, str, str, tuple[tuple[UUID, str, str, str, str], ...]]:
    binding_id = uuid4()
    binding_pub_id = _pub("bnd", binding_id)
    owner_handle = f"submission-owner-{token}"
    resource_rows: list[tuple[UUID, str, str, str, str]] = []
    resource_refs: dict[str, str] = {}
    for ordinal, resource_kind in enumerate(
        ("governed_account", "browser_owner", "browser_profile", "web_session")
    ):
        registration_id = uuid4()
        resource_pub_id = _pub("rrg", registration_id)
        resource_role = resource_kind
        mapping_revision = f"submission-mapping-{token}-{ordinal}"
        connection.execute(
            """
            INSERT INTO platform.resource_registration
              (id,pub_id,tenant_id,version,created_at,updated_at,resource_kind,
               display_mask,capabilities_json,region,concurrency_limit,state,
               last_heartbeat_at,project_id,resource_schema_version,
               resource_revision,owner_gateway_kind,owner_gateway_revision,
               opaque_owner_handle,attestation_revision,route_policy_revision,
               resource_fingerprint,approved_at)
            VALUES
              (%s,%s,%s,1,%s,%s,%s,%s,'{}','cn',1,'active',%s,%s,
               'collection-resource-v2',%s,'resident_browser','browser-owner-v1',
               %s,'attestation-v1','route-v1',%s,%s)
            """,
            (
                registration_id,
                resource_pub_id,
                tenant_id,
                now,
                now,
                resource_kind,
                f"submission-{resource_kind}",
                now,
                project_id,
                f"submission-{resource_kind}-{token}",
                owner_handle,
                _hash(f"submission-resource-{resource_kind}-{token}"),
                now,
            ),
        )
        resource_refs[resource_kind] = resource_pub_id
        resource_rows.append(
            (
                registration_id,
                resource_pub_id,
                resource_kind,
                resource_role,
                mapping_revision,
            )
        )

    connection.execute(
        """
        INSERT INTO platform.collection_binding_revision_v2
          (id,pub_id,tenant_id,project_id,parent_binding_revision_id,
           schema_version,binding_key,binding_revision,binding_policy_revision,
           lifecycle_state,lifecycle_reason,platform,collection_surface,
           product_variant,capability_registry_id,capability_registry_revision,
           quota_registry_id,quota_registry_revision,quota_policy_revision,
           region_policy_revision,route_policy_revision,resource_policy_revision,
           readiness_revision,required_resource_kinds_json,
           credential_references_json,canonical_json,binding_hash,owner_pub_id,
           approved_by_pub_id,approval_pub_id,approved_at,effective_from,expires_at)
        SELECT %s,%s,tenant_id,project_id,NULL,schema_version,
               binding_key || %s,1,binding_policy_revision,'candidate',
               'submission_repository_integration',platform,collection_surface,
               product_variant,capability_registry_id,capability_registry_revision,
               quota_registry_id,quota_registry_revision,quota_policy_revision,
               region_policy_revision,route_policy_revision,resource_policy_revision,
               readiness_revision,required_resource_kinds_json,
               credential_references_json,'{}',%s,'submission-integration-owner',
               'submission-integration-reviewer',NULL,%s,effective_from,expires_at
          FROM platform.collection_binding_revision_v2
         WHERE id=%s AND tenant_id=%s AND project_id=%s
        """,
        (
            binding_id,
            binding_pub_id,
            f"-submission-{token}",
            _hash(f"submission-binding-{token}"),
            now,
            source_binding_id,
            tenant_id,
            project_id,
        ),
    )
    web_binding_id = uuid4()
    connection.execute(
        """
        INSERT INTO platform.collection_web_binding_v2
          (id,pub_id,tenant_id,project_id,binding_revision_id,
           collection_surface,governed_account_ref,browser_owner_handle,
           browser_profile_ref,browser_profile_revision,web_session_ref,
           web_session_revision,approved_host_catalog_id,
           approved_host_catalog_revision,relay_policy_revision,
           constraints_revision,login_state,captcha_state,risk_state,
           human_assist_state,relay_required)
        VALUES
          (%s,%s,%s,%s,%s,'consumer_web',%s,%s,%s,'profile-v1',%s,
           'session-v1','hosts-v1','hosts-revision-v1','relay-v1',
           'constraints-v1','ready','ready','ready','ready',false)
        """,
        (
            web_binding_id,
            _pub("wbd", web_binding_id),
            tenant_id,
            project_id,
            binding_id,
            resource_refs["governed_account"],
            owner_handle,
            resource_refs["browser_profile"],
            resource_refs["web_session"],
        ),
    )
    capability_mapping_id = uuid4()
    connection.execute(
        """
        INSERT INTO platform.collection_binding_capability
          (id,pub_id,tenant_id,project_id,binding_revision_id,
           capability_declaration_id,capability_revision,platform,
           collection_surface,product_variant,interaction_mode,
           requirement_state,ordinal)
        SELECT %s,%s,tenant_id,project_id,%s,capability_declaration_id,
               capability_revision,platform,collection_surface,product_variant,
               interaction_mode,requirement_state,ordinal
          FROM platform.collection_binding_capability
         WHERE binding_revision_id=%s AND tenant_id=%s AND project_id=%s
        """,
        (
            capability_mapping_id,
            _pub("bcp", capability_mapping_id),
            binding_id,
            source_binding_id,
            tenant_id,
            project_id,
        ),
    )
    for ordinal, row in enumerate(resource_rows):
        registration_id, resource_pub_id, resource_kind, resource_role, mapping_revision = row
        mapping_id = uuid4()
        connection.execute(
            """
            INSERT INTO platform.collection_binding_resource
              (id,pub_id,tenant_id,project_id,binding_revision_id,
               resource_registration_id,resource_pub_id,resource_kind,
               resource_role,ordinal,required,adoption_required,mapping_revision)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,false,%s)
            """,
            (
                mapping_id,
                _pub("brs", mapping_id),
                tenant_id,
                project_id,
                binding_id,
                registration_id,
                resource_pub_id,
                resource_kind,
                resource_role,
                ordinal,
                mapping_revision,
            ),
        )
    quota_rows = connection.execute(
        """
        SELECT quota_scope_policy_id,quota_registry_id,scope_policy_key,
               scope_kind,scope_subject_id,policy_revision,applicability_key,
               quota_units,ordinal
          FROM platform.collection_binding_quota_scope
         WHERE binding_revision_id=%s AND tenant_id=%s AND project_id=%s
         ORDER BY ordinal
        """,
        (source_binding_id, tenant_id, project_id),
    ).fetchall()
    for quota_row in quota_rows:
        mapping_id = uuid4()
        connection.execute(
            """
            INSERT INTO platform.collection_binding_quota_scope
              (id,pub_id,tenant_id,project_id,binding_revision_id,
               quota_scope_policy_id,quota_registry_id,scope_policy_key,
               scope_kind,scope_subject_id,policy_revision,applicability_key,
               quota_units,ordinal)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                mapping_id,
                _pub("bqs", mapping_id),
                tenant_id,
                project_id,
                binding_id,
                *quota_row,
            ),
        )
    connection.execute(
        """
        UPDATE platform.collection_binding_revision_v2
           SET lifecycle_state='suspended',suspended_at=%s,
               lifecycle_reason='submission_integration_replaced',
               version=version+1,updated_at=%s
         WHERE id=%s AND tenant_id=%s AND project_id=%s
        """,
        (now, now, source_binding_id, tenant_id, project_id),
    )
    activated = connection.execute(
        """
        UPDATE platform.collection_binding_revision_v2
           SET lifecycle_state='active',activated_at=%s,
               lifecycle_reason='submission_integration_active',
               version=version+1,updated_at=%s
         WHERE id=%s AND tenant_id=%s AND project_id=%s
         RETURNING id
        """,
        (now, now, binding_id, tenant_id, project_id),
    ).fetchone()
    assert activated == (binding_id,)
    return binding_id, binding_pub_id, owner_handle, tuple(resource_rows)


def _create_leases(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    tenant_id: UUID,
    project_id: UUID,
    operation_id: UUID,
    binding_id: UUID,
    resources: tuple[tuple[UUID, str, str, str, str], ...],
    now: datetime,
    expires_at: datetime,
) -> tuple[_ResourceLease, ...]:
    leases: list[_ResourceLease] = []
    for ordinal, row in enumerate(resources):
        registration_id, resource_pub_id, resource_kind, resource_role, mapping_revision = row
        capacity_id, lease_id = uuid4(), uuid4()
        lease_pub_id = _pub("rle", lease_id)
        connection.execute(
            """
            INSERT INTO platform.collection_resource_capacity_unit
              (id,pub_id,tenant_id,project_id,resource_registration_id,
               resource_pub_id,resource_kind,capacity_unit_key,unit_ordinal,
               capacity_state,current_fencing_token,owner_gateway_revision,
               last_heartbeat_at,state_reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'candidate',0,
                    'browser-owner-v1',%s,'submission_capacity_created')
            """,
            (
                capacity_id,
                _pub("rcu", capacity_id),
                tenant_id,
                project_id,
                registration_id,
                resource_pub_id,
                resource_kind,
                f"submission-capacity-{ordinal}-{capacity_id.hex}",
                1,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE platform.collection_resource_capacity_unit
               SET capacity_state='available',state_reason='submission_available',
                   version=version+1,updated_at=%s
             WHERE id=%s AND tenant_id=%s AND project_id=%s
            """,
            (now, capacity_id, tenant_id, project_id),
        )
        connection.execute(
            """
            UPDATE platform.collection_resource_capacity_unit
               SET capacity_state='leased',current_fencing_token=1,
                   state_reason='submission_leased',version=version+1,updated_at=%s
             WHERE id=%s AND tenant_id=%s AND project_id=%s
            """,
            (now, capacity_id, tenant_id, project_id),
        )
        connection.execute(
            """
            INSERT INTO platform.resource_lease
              (id,pub_id,tenant_id,version,created_at,updated_at,resource_kind,
               resource_pub_id,holder,capability_json,region,fencing_token,
               expires_at,released_at,project_id,lease_schema_version,
               resource_registration_id,capacity_unit_id,operation_id,
               binding_revision_id,lease_key,lease_attempt,lease_state,
               acquired_at,heartbeat_at,revoked_at,owner_gateway_revision,
               reconciliation_reason)
            VALUES (%s,%s,%s,1,%s,%s,%s,%s,'submission-integration-worker',
                    '{}','cn',1,%s,NULL,%s,'collection-resource-lease-v2',
                    %s,%s,%s,%s,%s,1,'active',%s,%s,NULL,
                    'browser-owner-v1',NULL)
            """,
            (
                lease_id,
                lease_pub_id,
                tenant_id,
                now,
                now,
                resource_kind,
                resource_pub_id,
                expires_at,
                project_id,
                registration_id,
                capacity_id,
                operation_id,
                binding_id,
                f"submission-lease-{ordinal}-{lease_id.hex}",
                now,
                now,
            ),
        )
        leases.append(
            _ResourceLease(
                registration_id=registration_id,
                resource_pub_id=resource_pub_id,
                resource_kind=resource_kind,
                resource_role=resource_role,
                mapping_revision=mapping_revision,
                capacity_id=capacity_id,
                lease_id=lease_id,
                lease_pub_id=lease_pub_id,
                acquired_at=now,
                expires_at=expires_at,
            )
        )
    return tuple(leases)


def _issue_execution_grant(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    tenant_id: UUID,
    project_id: UUID,
    operation_id: UUID,
    binding_id: UUID,
    quota_registry_id: UUID,
    quota_reservation_id: UUID,
    owner_handle: str,
    leases: tuple[_ResourceLease, ...],
    token: str,
    issued_at: datetime,
    expires_at: datetime,
) -> tuple[UUID, str, str]:
    basis = connection.execute(
        """
        SELECT operation.campaign_id,operation.campaign_target_id,
               operation.sampling_leg_id,operation.primary_slot_id,
               operation.platform,operation.collection_surface,
               operation.product_variant,operation.province_code,
               operation.interaction_mode,campaign.config_revision_id,
               capability.id,capability.capability_revision
          FROM platform.collection_submission_operation AS operation
          JOIN platform.collection_campaign AS campaign
            ON campaign.id=operation.campaign_id
           AND campaign.tenant_id=operation.tenant_id
           AND campaign.project_id=operation.project_id
          JOIN platform.collection_binding_capability AS capability
            ON capability.binding_revision_id=%s
           AND capability.tenant_id=operation.tenant_id
           AND capability.project_id=operation.project_id
           AND capability.platform=operation.platform
           AND capability.collection_surface=operation.collection_surface
           AND capability.product_variant=operation.product_variant
           AND capability.interaction_mode=operation.interaction_mode
         WHERE operation.id=%s AND operation.tenant_id=%s
           AND operation.project_id=%s
        """,
        (binding_id, operation_id, tenant_id, project_id),
    ).fetchone()
    assert basis is not None and len(basis) == 12
    grant_id = uuid4()
    grant_pub_id = _pub("egr", grant_id)
    grant_hash = _hash(f"submission-grant-{token}")
    connection.execute(
        """
        INSERT INTO platform.collection_execution_grant_v2
          (id,pub_id,tenant_id,project_id,schema_version,grant_key,
           grant_revision,grant_state,config_revision_id,campaign_id,
           campaign_target_id,sampling_leg_id,primary_slot_id,operation_id,
           binding_revision_id,binding_revision,binding_capability_id,
           capability_revision,quota_registry_id,quota_reservation_id,
           platform,collection_surface,product_variant,province_code,
           interaction_mode,route_policy_revision,resource_policy_revision,
           workflow_contract_version,adapter_revision,gateway_protocol_revision,
           worker_build_id,agent_revision,allowed_actions_json,grant_hash,
           issued_by_pub_id,issuance_reason,issued_at,expires_at)
        VALUES
          (%s,%s,%s,%s,'collection-execution-grant-v1',%s,1,'assembling',
           %s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,
           'route-v1','resource-v1','submission-workflow-v2','adapter-v1',
           'browser-owner-v1','integration-build-v1',NULL,
           '["capture_existing","submit_query"]',%s,
           'submission-integration-worker','integration_issue',NULL,%s)
        """,
        (
            grant_id,
            grant_pub_id,
            tenant_id,
            project_id,
            f"submission-grant-{token}",
            basis[9],
            basis[0],
            basis[1],
            basis[2],
            basis[3],
            operation_id,
            binding_id,
            basis[10],
            basis[11],
            quota_registry_id,
            quota_reservation_id,
            basis[4],
            basis[5],
            basis[6],
            basis[7],
            basis[8],
            grant_hash,
            expires_at,
        ),
    )
    web_grant_id = uuid4()
    web = connection.execute(
        """
        SELECT governed_account_ref,browser_profile_ref,browser_profile_revision,
               web_session_ref,web_session_revision,approved_host_catalog_id
          FROM platform.collection_web_binding_v2
         WHERE binding_revision_id=%s AND tenant_id=%s AND project_id=%s
        """,
        (binding_id, tenant_id, project_id),
    ).fetchone()
    assert web is not None and len(web) == 6
    connection.execute(
        """
        INSERT INTO platform.collection_web_execution_grant_v2
          (id,pub_id,tenant_id,project_id,execution_grant_id,
           collection_surface,browser_owner_handle,governed_account_ref,
           browser_profile_ref,browser_profile_revision,web_session_ref,
           web_session_revision,approved_host_catalog_id)
        VALUES (%s,%s,%s,%s,%s,'consumer_web',%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            web_grant_id,
            _pub("weg", web_grant_id),
            tenant_id,
            project_id,
            grant_id,
            owner_handle,
            *web,
        ),
    )
    for ordinal, lease in enumerate(leases):
        grant_resource_id = uuid4()
        connection.execute(
            """
            INSERT INTO platform.collection_execution_grant_resource
              (id,pub_id,tenant_id,project_id,execution_grant_id,operation_id,
               binding_revision_id,resource_registration_id,capacity_unit_id,
               resource_lease_id,resource_pub_id,resource_kind,resource_role,
               resource_ordinal,binding_resource_mapping_revision,
               owner_gateway_handle,fence_generation,lease_expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
            """,
            (
                grant_resource_id,
                _pub("egr", grant_resource_id),
                tenant_id,
                project_id,
                grant_id,
                operation_id,
                binding_id,
                lease.registration_id,
                lease.capacity_id,
                lease.lease_id,
                lease.resource_pub_id,
                lease.resource_kind,
                lease.resource_role,
                ordinal,
                lease.mapping_revision,
                owner_handle,
                lease.expires_at,
            ),
        )
    issued = connection.execute(
        """
        UPDATE platform.collection_execution_grant_v2
           SET grant_state='issued',issued_at=%s,version=version+1,updated_at=%s
         WHERE id=%s AND tenant_id=%s AND project_id=%s
           AND grant_state='assembling'
         RETURNING id
        """,
        (issued_at, issued_at, grant_id, tenant_id, project_id),
    ).fetchone()
    assert issued == (grant_id,)
    connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return grant_id, grant_pub_id, grant_hash


def _prepare(
    fixture: _SubmissionFixture,
    *,
    args: tuple[object, ...] | None = None,
) -> tuple[UUID, UUID, bool]:
    return _execute_prepare(fixture.dsn, args or fixture.prepare_args)


def _execute_prepare(
    dsn: str,
    args: tuple[object, ...],
) -> tuple[UUID, UUID, bool]:
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            _set_scope(connection, cast(UUID, args[0]), role="geo_worker")
            row = connection.execute(PREPARE_SQL, args).fetchone()
    assert row is not None and len(row) == 3
    return UUID(str(row[0])), UUID(str(row[1])), bool(row[2])


def _claim_once(fixture: _SubmissionFixture, barrier: Barrier) -> bool:
    try:
        with psycopg.connect(fixture.dsn) as connection:
            with connection.transaction():
                _set_scope(connection, fixture.tenant_id, role="geo_worker")
                barrier.wait(timeout=10)
                row = connection.execute(CLAIM_SQL, fixture.claim_args).fetchone()
    except psycopg.Error as exc:
        # Both exact replay (false) and a row-lock CAS loser are acceptable;
        # neither may report a second freshly-applied irreversible claim.
        message = str(exc).lower()
        if not any(
            marker in message
            for marker in (
                "submission operation is not claimable",
                "submission claim compare-and-swap lost",
                "duplicate key value violates unique constraint",
            )
        ):
            raise
        return False
    assert row is not None and len(row) == 3
    assert str(row[1]) == str(fixture.claim_args[3])
    return bool(row[2])


def _worker_repository(fixture: _SubmissionFixture) -> PostgresSubmissionRepository:
    def connect() -> RepositoryConnection:
        connection = psycopg.connect(fixture.dsn)
        connection.execute("SET ROLE geo_worker")
        connection.commit()
        return cast(RepositoryConnection, connection)

    return PostgresSubmissionRepository(
        scope=RepositoryScope(
            tenant_id=fixture.tenant_id,
            project_id=fixture.project_id,
        ),
        connection_factory=connect,
        prepared_by_pub_id="submission-integration-worker",
    )


def _unused_primary_admission_args(fixture: _SubmissionFixture) -> tuple[object, ...]:
    with psycopg.connect(fixture.dsn) as connection:
        row = connection.execute(
            """
            SELECT campaign.pub_id,target.target_key,slot.pub_id,slot.slot_key,
                   leg.leg_key,slot.platform,slot.collection_surface,
                   slot.product_variant
              FROM platform.collection_submission_operation seed
              JOIN platform.collection_campaign campaign
                ON campaign.id=seed.campaign_id
               AND campaign.tenant_id=seed.tenant_id
               AND campaign.project_id=seed.project_id
              JOIN platform.collection_primary_slot slot
                ON slot.campaign_id=campaign.id
               AND slot.tenant_id=campaign.tenant_id
               AND slot.project_id=campaign.project_id
               AND slot.id<>seed.primary_slot_id
               AND slot.slot_role='primary'
              JOIN platform.collection_campaign_target target
                ON target.id=slot.campaign_target_id
               AND target.tenant_id=slot.tenant_id
               AND target.project_id=slot.project_id
              JOIN platform.collection_sampling_leg leg
                ON leg.id=slot.sampling_leg_id
               AND leg.tenant_id=slot.tenant_id
               AND leg.project_id=slot.project_id
             WHERE seed.id=%s AND seed.tenant_id=%s AND seed.project_id=%s
             ORDER BY slot.slot_ordinal
             LIMIT 1
            """,
            (fixture.operation_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
    assert row is not None and len(row) == 8
    operation_pub_id = _pub("opr", uuid4())
    material = OperationKeyMaterial(
        tenant_id=fixture.tenant_id,
        project_id=fixture.project_id,
        campaign_pub_id=str(row[0]),
        slot_pub_id=str(row[2]),
        target_key=str(row[1]),
        leg_key=str(row[4]),
        logical_item_key=str(row[3]),
        generation=1,
        operation_policy_revision="operation-policy-v1",
    )
    return (
        fixture.tenant_id,
        fixture.project_id,
        operation_pub_id,
        material.generation,
        deterministic_operation_key(material),
        material.operation_policy_revision,
        datetime.now(UTC),
        material.slot_pub_id,
        material.logical_item_key,
        material.campaign_pub_id,
        material.target_key,
        material.leg_key,
        str(row[5]),
        str(row[6]),
        str(row[7]),
    )


def _submission_work(fixture: _SubmissionFixture) -> SubmissionWorkItem:
    return SubmissionWorkItem(
        prepared=PreparedSubmissionRef(
            workflow=WorkflowOperationInput(
                operation=fixture.operation_ref,
                expected_state_version=1,
            ),
            reservation_pub_id=fixture.reservation_pub_id,
        ),
        grant_pub_id=fixture.execution_grant_pub_id,
        lease_pub_ids=tuple(lease.lease_pub_id for lease in fixture.leases),
        cursor_ref="submission-integration-cursor",
        claim_pub_id=str(fixture.claim_args[3]),
        reconciliation_claim_ref="submission-integration-reconciliation",
        capture_attempt_ref="submission-integration-capture",
    )


def _preflight_transition(
    fixture: _SubmissionFixture,
    current: SubmissionOperationTruth,
) -> TerminalSubmissionTransition:
    assert current.send_state is SendState.NOT_SENT
    assert current.claim is None
    assert current.terminal is None
    assert current.identity == fixture.identity
    resolved_at = datetime.now(UTC)
    terminal = TerminalSubmissionTruth(
        send_state=SendState.CONFIRMED_NOT_SENT,
        reason=TerminalReason.PREFLIGHT_NOT_SENT,
        boundary_entered=False,
        evidence_ref=f"preflight-evidence-{fixture.operation_id.hex[:16]}",
        evidence_sha256=_hash(f"preflight-evidence-{fixture.operation_id}"),
        resolved_at=resolved_at,
        terminated_fence_set_sha256=fixture.authority.fence_set_sha256,
    )
    updated = SubmissionOperationTruth(
        identity=current.identity,
        send_state=terminal.send_state,
        state_version=current.state_version + 1,
        prepared_at=current.prepared_at,
        terminal=terminal,
    )
    payload_hash = sha256(canonical_json(terminal).encode()).hexdigest()
    event_type = "collection.submission.terminal"
    outbox = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type=event_type,
            aggregate_ref=fixture.operation_ref.operation_pub_id,
            aggregate_version=2,
            payload_sha256=payload_hash,
        ),
        event_type=event_type,
        aggregate_ref=fixture.operation_ref.operation_pub_id,
        aggregate_version=2,
        payload_sha256=payload_hash,
        occurred_at=resolved_at,
    )
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=QuotaTerminalEffect.RELEASE,
        outbox=outbox,
    )


def _submitted_transition(
    fixture: _SubmissionFixture,
    current: SubmissionOperationTruth,
) -> TerminalSubmissionTransition:
    assert current.send_state is SendState.SENDING
    assert current.claim is not None
    assert current.terminal is None
    assert current.identity == fixture.identity
    resolved_at = datetime.now(UTC)
    terminal = TerminalSubmissionTruth(
        send_state=SendState.CONFIRMED_SENT,
        reason=TerminalReason.SUBMITTED,
        boundary_entered=True,
        evidence_ref=f"submission-evidence-{fixture.operation_id.hex[:16]}",
        evidence_sha256=_hash(f"submission-evidence-{fixture.operation_id}"),
        resolved_at=resolved_at,
        provider_submission_ref=f"provider-submission-{fixture.operation_id.hex[:16]}",
    )
    updated = SubmissionOperationTruth(
        identity=current.identity,
        send_state=terminal.send_state,
        state_version=current.state_version + 1,
        prepared_at=current.prepared_at,
        claim=current.claim,
        terminal=terminal,
    )
    payload_hash = sha256(canonical_json(terminal).encode()).hexdigest()
    event_type = "collection.submission.terminal"
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=QuotaTerminalEffect.SETTLE_CONSUMED,
        outbox=OutboxEventRef(
            outbox_key=deterministic_outbox_key(
                event_type=event_type,
                aggregate_ref=fixture.operation_ref.operation_pub_id,
                aggregate_version=3,
                payload_sha256=payload_hash,
            ),
            event_type=event_type,
            aggregate_ref=fixture.operation_ref.operation_pub_id,
            aggregate_version=3,
            payload_sha256=payload_hash,
            occurred_at=resolved_at,
        ),
    )


def _unknown_transition(
    fixture: _SubmissionFixture,
    current: SubmissionOperationTruth,
) -> TerminalSubmissionTransition:
    assert current.send_state is SendState.SENDING
    assert current.claim is not None
    assert current.terminal is None
    assert current.identity == fixture.identity
    resolved_at = datetime.now(UTC)
    terminal = TerminalSubmissionTruth(
        send_state=SendState.SEND_UNKNOWN,
        reason=TerminalReason.SEND_UNKNOWN,
        boundary_entered=True,
        evidence_ref=f"reconciliation-evidence-{fixture.operation_id.hex[:16]}",
        evidence_sha256=_hash(f"reconciliation-evidence-{fixture.operation_id}"),
        resolved_at=resolved_at,
    )
    updated = SubmissionOperationTruth(
        identity=current.identity,
        send_state=terminal.send_state,
        state_version=current.state_version + 1,
        prepared_at=current.prepared_at,
        claim=current.claim,
        terminal=terminal,
    )
    payload_hash = sha256(canonical_json(terminal).encode()).hexdigest()
    event_type = "collection.submission.terminal"
    return TerminalSubmissionTransition(
        operation=updated,
        quota_effect=QuotaTerminalEffect.SETTLE_UNKNOWN,
        outbox=OutboxEventRef(
            outbox_key=deterministic_outbox_key(
                event_type=event_type,
                aggregate_ref=fixture.operation_ref.operation_pub_id,
                aggregate_version=updated.state_version,
                payload_sha256=payload_hash,
            ),
            event_type=event_type,
            aggregate_ref=fixture.operation_ref.operation_pub_id,
            aggregate_version=updated.state_version,
            payload_sha256=payload_hash,
            occurred_at=resolved_at,
        ),
    )


def _capture_context(fixture: _SubmissionFixture) -> ResolvedSubmissionContext:
    return ResolvedSubmissionContext(
        prepare=PrepareSubmissionCommand(
            identity=fixture.identity,
            prepared_at=cast(datetime, fixture.prepare_args[-1]),
        ),
        authority=fixture.authority,
        owner_dispatch_ref=str(fixture.claim_args[14]),
        owner_wal_evidence_sha256=str(fixture.claim_args[15]),
        capture_policy_revision="capture-policy-v1",
    )


def _seed_additional_frozen_campaign(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    token: str,
    capability_registry_revision: str,
    capability_revision: str,
    binding_policy_revision: str,
) -> tuple[UUID, tuple[tuple[object, ...], ...]]:
    """Reuse the quota seed's materializer at the next real config revision."""

    with psycopg.connect(dsn) as connection:
        parent = connection.execute(
            """
            SELECT id,revision
              FROM platform.collection_config_revision_v2
             WHERE tenant_id=%s AND project_id=%s
             ORDER BY revision DESC
             LIMIT 1
            """,
            (tenant_id, project_id),
        ).fetchone()
    assert parent is not None and len(parent) == 2
    parent_id = UUID(str(parent[0]))
    next_revision = int(parent[1]) + 1

    def freeze_next_revision(request: ConfigFreezeRequest) -> FrozenConfigRevision:
        registry = request.capability_registry.model_copy(
            update={
                "registry_revision": capability_registry_revision,
                "capabilities": tuple(
                    declaration.model_copy(update={"capability_revision": capability_revision})
                    for declaration in request.capability_registry.capabilities
                ),
            }
        )
        return freeze_config(
            request.model_copy(
                update={
                    "revision": next_revision,
                    "parent_revision_id": parent_id,
                    "capability_registry": registry,
                }
            )
        )

    def freeze_matching_campaign(request: CampaignFreezeRequest) -> CampaignAssemblyBlueprint:
        return freeze_campaign(
            request.model_copy(update={"binding_policy_revision": binding_policy_revision})
        )

    with (
        patch.object(quota_integration, "freeze_config", freeze_next_revision),
        patch.object(quota_integration, "freeze_campaign", freeze_matching_campaign),
    ):
        return quota_integration._seed_frozen_campaign(
            dsn,
            tenant_id=tenant_id,
            project_id=project_id,
            token=token,
        )


def _seed_submission_fixture(dsn: str) -> _SubmissionFixture:
    token = uuid4().hex[:8]
    service = _seed_service_fixture(dsn)
    with psycopg.connect(dsn) as connection:
        capability = connection.execute(
            """
            SELECT binding.capability_registry_revision,
                   capability.capability_revision,
                   binding.binding_policy_revision
              FROM platform.collection_binding_revision_v2 AS binding
              JOIN platform.collection_binding_capability AS capability
                ON capability.binding_revision_id=binding.id
               AND capability.tenant_id=binding.tenant_id
               AND capability.project_id=binding.project_id
             WHERE binding.id=%s AND binding.tenant_id=%s AND binding.project_id=%s
            """,
            (service.binding_id, service.tenant_id, service.project_id),
        ).fetchone()
    assert capability is not None and len(capability) == 3
    campaign_id, campaign_slots = _seed_additional_frozen_campaign(
        dsn,
        tenant_id=service.tenant_id,
        project_id=service.project_id,
        token=f"submission-{token}",
        capability_registry_revision=str(capability[0]),
        capability_revision=str(capability[1]),
        binding_policy_revision=str(capability[2]),
    )
    operation_identity_seed = uuid4()
    operation_pub_id = _pub("opr", operation_identity_seed)
    (
        slot_id,
        campaign_target_id,
        sampling_leg_id,
        slot_key,
        platform,
        collection_surface,
        product_variant,
        province_code,
        interaction_mode,
    ) = campaign_slots[0]
    request_manifest = RequestManifest(
        request_protocol_version="provider-request-v1",
        request_schema_revision="adapter-request-v1",
        request_payload_ref=f"request-content-{token}",
        request_payload_sha256=_hash(f"request-payload-{token}"),
    )
    request_manifest_hash = request_manifest_digest(request_manifest)
    now = datetime.now(UTC)
    prepared_at = now
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            _ensure_s10(connection)
            _set_scope(connection, service.tenant_id)
            binding_id, binding_pub_id, owner_handle, resources = _clone_binding_with_single_owner(
                connection,
                tenant_id=service.tenant_id,
                project_id=service.project_id,
                source_binding_id=service.binding_id,
                token=token,
                now=now,
            )
            operation_protocol_row = connection.execute(
                """
                SELECT campaign.pub_id,target.target_key,slot.pub_id,leg.leg_key
                  FROM platform.collection_campaign AS campaign
                  JOIN platform.collection_campaign_target AS target
                    ON target.id=%s
                   AND target.tenant_id=campaign.tenant_id
                   AND target.project_id=campaign.project_id
                  JOIN platform.collection_primary_slot AS slot
                    ON slot.id=%s
                   AND slot.tenant_id=campaign.tenant_id
                   AND slot.project_id=campaign.project_id
                  JOIN platform.collection_sampling_leg AS leg
                    ON leg.id=%s
                   AND leg.tenant_id=campaign.tenant_id
                   AND leg.project_id=campaign.project_id
                 WHERE campaign.id=%s AND campaign.tenant_id=%s
                   AND campaign.project_id=%s
                """,
                (
                    campaign_target_id,
                    slot_id,
                    sampling_leg_id,
                    campaign_id,
                    service.tenant_id,
                    service.project_id,
                ),
            ).fetchone()
            assert operation_protocol_row is not None and len(operation_protocol_row) == 4
            surface_product = SurfaceProductRef(
                platform=str(platform),
                collection_surface=CollectionSurface(str(collection_surface)),
                product_variant=str(product_variant),
                target_key=str(operation_protocol_row[1]),
            )
            operation_material = OperationKeyMaterial(
                tenant_id=service.tenant_id,
                project_id=service.project_id,
                campaign_pub_id=str(operation_protocol_row[0]),
                slot_pub_id=str(operation_protocol_row[2]),
                target_key=surface_product.target_key,
                leg_key=str(operation_protocol_row[3]),
                logical_item_key=str(slot_key),
                generation=1,
                operation_policy_revision="operation-policy-v1",
            )
            operation_key = deterministic_operation_key(operation_material)
            provider_idempotency_key = deterministic_provider_idempotency_key(operation_key)
            identity = OperationIdentity(
                material=operation_material,
                surface_product=surface_product,
                operation_pub_id=operation_pub_id,
                operation_key=operation_key,
                request_manifest=request_manifest,
                request_manifest_sha256=request_manifest_hash,
                provider_idempotency_key=provider_idempotency_key,
            )
            create_operation_args: tuple[object, ...] = (
                service.tenant_id,
                service.project_id,
                operation_pub_id,
                1,
                operation_key,
                "operation-policy-v1",
                prepared_at,
                operation_material.slot_pub_id,
                operation_material.logical_item_key,
                operation_material.campaign_pub_id,
                operation_material.target_key,
                operation_material.leg_key,
                surface_product.platform,
                surface_product.collection_surface.value,
                surface_product.product_variant,
            )
            _set_scope(connection, service.tenant_id, role="geo_worker")
            created = connection.execute(CREATE_OPERATION_SQL, create_operation_args).fetchone()
            assert created is not None and len(created) == 2 and bool(created[1])
            operation_id = UUID(str(created[0]))
            replay = connection.execute(CREATE_OPERATION_SQL, create_operation_args).fetchone()
            assert replay == (operation_id, False)
            reservation = reserve_quota_after_operation_admission(
                cast(ConnectionProtocol, connection),
                ReserveQuotaRequest(
                    tenant_id=service.tenant_id,
                    project_id=service.project_id,
                    operation_id=operation_id,
                    binding_id=binding_id,
                    registry_id=service.registry_id,
                    requested_units=1,
                ),
            )
            assert reservation.reserved and reservation.reservation_id is not None
            reservation_id = reservation.reservation_id
            prepare_args: tuple[object, ...] = (
                service.tenant_id,
                service.project_id,
                operation_id,
                1,
                request_manifest.request_payload_sha256,
                request_manifest_hash,
                request_manifest.request_protocol_version,
                request_manifest.request_schema_revision,
                request_manifest.request_payload_ref,
                _hash(provider_idempotency_key),
                "submission-integration-worker",
                prepared_at,
            )
            prepared_row = connection.execute(PREPARE_SQL, prepare_args).fetchone()
            assert prepared_row is not None and len(prepared_row) == 3
            prepared_manifest_id = UUID(str(prepared_row[0]))
            capture_truth_id = UUID(str(prepared_row[1]))
            assert bool(prepared_row[2])
    durable_operation_ref = operation_ref(identity)

    lease_acquired_at = datetime.now(UTC)
    lease_expires_at = lease_acquired_at + timedelta(hours=2)
    grant_issued_at = lease_acquired_at
    grant_expires_at = lease_acquired_at + timedelta(hours=1)
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            _set_scope(connection, service.tenant_id)
            leases = _create_leases(
                connection,
                tenant_id=service.tenant_id,
                project_id=service.project_id,
                operation_id=operation_id,
                binding_id=binding_id,
                resources=resources,
                now=lease_acquired_at,
                expires_at=lease_expires_at,
            )
            grant_id, grant_pub_id, grant_hash = _issue_execution_grant(
                connection,
                tenant_id=service.tenant_id,
                project_id=service.project_id,
                operation_id=operation_id,
                binding_id=binding_id,
                quota_registry_id=service.registry_id,
                quota_reservation_id=reservation_id,
                owner_handle=owner_handle,
                leases=leases,
                token=token,
                issued_at=grant_issued_at,
                expires_at=grant_expires_at,
            )
            fence_row = connection.execute(
                "SELECT platform.collection_dispatch_fence_set_hash_s10(%s,%s,%s)",
                (service.tenant_id, service.project_id, grant_id),
            ).fetchone()
            operation_row = connection.execute(
                "SELECT pub_id FROM platform.collection_submission_operation "
                "WHERE id=%s AND tenant_id=%s AND project_id=%s",
                (operation_id, service.tenant_id, service.project_id),
            ).fetchone()
            reservation_row = connection.execute(
                "SELECT pub_id FROM platform.collection_quota_reservation "
                "WHERE id=%s AND tenant_id=%s AND project_id=%s",
                (reservation_id, service.tenant_id, service.project_id),
            ).fetchone()
    assert fence_row is not None and isinstance(fence_row[0], str)
    assert operation_row is not None and isinstance(operation_row[0], str)
    assert reservation_row is not None and isinstance(reservation_row[0], str)
    fence_set_hash = fence_row[0]
    claimed_at = datetime.now(UTC)
    canonical_fences = tuple(
        LeaseFenceRef(
            acquired_at=lease.acquired_at,
            binding_resource_pub_id=lease.resource_pub_id,
            expires_at=lease.expires_at,
            generation=1,
            lease_pub_id=lease.lease_pub_id,
            owner_handle=owner_handle,
            resource_role=lease.resource_role,
        )
        for lease in sorted(
            leases,
            key=lambda item: (item.resource_role, item.resource_pub_id, item.lease_pub_id),
        )
    )
    authority = OwnerAuthorityRef(
        binding_revision_pub_id=binding_pub_id,
        checked_at=claimed_at,
        fence_set_sha256=lease_fence_set_digest(canonical_fences),
        grant_pub_id=grant_pub_id,
        grant_revision=1,
        lease_fences=canonical_fences,
        owner_handle=owner_handle,
        valid_until=grant_expires_at,
    )
    assert authority.fence_set_sha256 == fence_set_hash
    authority_json = canonical_json(authority)
    claim_args: tuple[object, ...] = (
        service.tenant_id,
        service.project_id,
        operation_id,
        f"claim-{token}",
        1,
        grant_id,
        1,
        grant_hash,
        fence_set_hash,
        owner_handle,
        authority_json,
        authority_digest(authority),
        f"dispatch-{token}",
        "browser-owner-v1",
        f"owner-dispatch-{token}",
        _hash(f"owner-wal-{token}"),
        claimed_at,
    )
    return _SubmissionFixture(
        dsn=dsn,
        tenant_id=service.tenant_id,
        project_id=service.project_id,
        operation_id=operation_id,
        operation_pub_id=operation_row[0],
        identity=identity,
        operation_ref=durable_operation_ref,
        surface_product=surface_product,
        binding_id=binding_id,
        binding_pub_id=binding_pub_id,
        owner_handle=owner_handle,
        execution_grant_id=grant_id,
        execution_grant_pub_id=grant_pub_id,
        grant_hash=grant_hash,
        fence_set_hash=fence_set_hash,
        authority=authority,
        reservation_pub_id=reservation_row[0],
        leases=leases,
        create_operation_args=create_operation_args,
        prepare_args=prepare_args,
        prepared_manifest_id=prepared_manifest_id,
        capture_truth_id=capture_truth_id,
        claim_args=claim_args,
    )


@pytest.fixture(scope="module")
def submission_fixture() -> _SubmissionFixture:
    return _seed_submission_fixture(_test_dsn())


def test_prepare_exact_replay_and_payload_drift_fail_closed(
    submission_fixture: _SubmissionFixture,
) -> None:
    replay = _prepare(submission_fixture)
    assert replay == (
        submission_fixture.prepared_manifest_id,
        submission_fixture.capture_truth_id,
        False,
    )
    drifted = list(submission_fixture.prepare_args)
    drifted[4] = _hash("drifted-request-payload")
    with pytest.raises(psycopg.Error, match="idempotency payload drifted"):
        _prepare(submission_fixture, args=tuple(drifted))


def test_concurrent_claim_has_one_fresh_winner_and_live_leases_block_reconciliation(
    submission_fixture: _SubmissionFixture,
) -> None:
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result(timeout=20)
            for future in (
                pool.submit(_claim_once, submission_fixture, barrier),
                pool.submit(_claim_once, submission_fixture, barrier),
            )
        )
    assert sorted(results) == [False, True]
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
            dispatch = connection.execute(
                """
                SELECT id,reconciliation_version
                  FROM platform.collection_submission_dispatch_v2
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                """,
                (
                    submission_fixture.operation_id,
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                ),
            ).fetchone()
            assert dispatch is not None
            with pytest.raises(psycopg.Error) as blocked:
                with connection.transaction():
                    connection.execute(
                        MARK_RECONCILIATION_READY_SQL,
                        (
                            submission_fixture.tenant_id,
                            submission_fixture.project_id,
                            submission_fixture.operation_id,
                            dispatch[0],
                            dispatch[1],
                            "owner-loss-proof",
                            _hash("owner-loss-proof"),
                            datetime.now(UTC),
                        ),
                    ).fetchone()
            message = str(blocked.value).lower()
            assert "terminated fenced authority" in message or (
                "lease" in message and ("active" in message or "terminated" in message)
            )
            persisted = connection.execute(
                """
                SELECT owner_execution_state,reconciliation_state
                  FROM platform.collection_submission_dispatch_v2
                 WHERE id=%s AND tenant_id=%s AND project_id=%s
                """,
                (dispatch[0], submission_fixture.tenant_id, submission_fixture.project_id),
            ).fetchone()
            active_leases = connection.execute(
                """
                SELECT count(*) FROM platform.resource_lease
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                   AND lease_state='active'
                """,
                (
                    submission_fixture.operation_id,
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                ),
            ).fetchone()
    assert persisted == ("active", "not_required")
    assert active_leases == (4,)

    terminated_at = datetime.now(UTC)
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            connection.execute(
                """
                UPDATE platform.resource_lease
                   SET lease_state='released',released_at=%s,
                       reconciliation_reason='integration_owner_lost',
                       version=version+1,updated_at=%s
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                   AND lease_state='active'
                """,
                (
                    terminated_at,
                    terminated_at,
                    submission_fixture.operation_id,
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                ),
            )
            connection.execute(
                """
                UPDATE platform.collection_resource_capacity_unit AS capacity
                   SET capacity_state='available',state_reason='integration_owner_lost',
                       version=capacity.version+1,updated_at=%s
                  FROM platform.collection_execution_grant_resource AS resource
                 WHERE resource.execution_grant_id=%s
                   AND resource.tenant_id=%s AND resource.project_id=%s
                   AND capacity.id=resource.capacity_unit_id
                   AND capacity.tenant_id=resource.tenant_id
                   AND capacity.project_id=resource.project_id
                   AND capacity.capacity_state='leased'
                """,
                (
                    terminated_at,
                    submission_fixture.execution_grant_id,
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                ),
            )

    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
            dispatch = connection.execute(
                """
                SELECT id,reconciliation_version
                  FROM platform.collection_submission_dispatch_v2
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                """,
                (
                    submission_fixture.operation_id,
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                ),
            ).fetchone()
            assert dispatch is not None
            marked = connection.execute(
                """
                SELECT platform.mark_collection_dispatch_reconciliation_ready_v2(
                  %s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP
                )
                """,
                (
                    submission_fixture.tenant_id,
                    submission_fixture.project_id,
                    submission_fixture.operation_id,
                    dispatch[0],
                    dispatch[1],
                    "owner-loss-proof",
                    _hash("owner-loss-proof"),
                ),
            ).fetchone()
    assert marked == (2,)

    work = _submission_work(submission_fixture)
    repository = _worker_repository(submission_fixture)
    sending = repository.load_operation(submission_fixture.operation_ref)
    assert sending is not None and sending.send_state is SendState.SENDING
    claim = repository.claim_reconciliation(work=work, operation=sending)
    assert claim.acquired and claim.owner_session_terminated
    transition = _unknown_transition(submission_fixture, sending)
    terminal = repository.atomic_terminal_and_quota(work, transition)
    assert terminal == transition.operation

    # A new repository instance represents a process crash after terminal commit.
    recovered_repository = _worker_repository(submission_fixture)
    recovered = recovered_repository.load_operation(submission_fixture.operation_ref)
    assert recovered == terminal
    assert terminal.terminal is not None
    recorded_at = terminal.terminal.resolved_at + timedelta(milliseconds=1)
    fact = SlotOutcomeFact(
        operation=submission_fixture.operation_ref,
        outcome=SlotOutcome.SEND_UNKNOWN,
        operation_state_version=terminal.state_version,
        is_final_primary=False,
        fact_version=1,
        recorded_at=recorded_at,
    )
    fact_hash = sha256(canonical_json(fact).encode()).hexdigest()
    event = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type="collection.slot.outcome",
            aggregate_ref=submission_fixture.operation_ref.operation_pub_id,
            aggregate_version=fact.fact_version,
            payload_sha256=fact_hash,
        ),
        event_type="collection.slot.outcome",
        aggregate_ref=submission_fixture.operation_ref.operation_pub_id,
        aggregate_version=fact.fact_version,
        payload_sha256=fact_hash,
        occurred_at=fact.recorded_at,
    )
    assert recovered_repository.atomic_fact_and_outbox(work, fact, event) == fact
    capture = recovered_repository.load_capture(submission_fixture.operation_ref)
    assert capture is not None and capture.capture_state is CaptureState.NOT_STARTED
    admission = recovered_repository.resolve_capture_admission(
        work=work,
        operation=terminal,
        capture=capture,
    )
    assert admission.decision is CaptureAdmissionDecision.RECONCILED_NO_AUTHORITY
    assert admission.reconciliation_claim_ref == work.reconciliation_claim_ref


def test_repository_preflight_terminal_atomically_releases_grant_leases_and_quota() -> None:
    fixture = _seed_submission_fixture(_test_dsn())
    repository = _worker_repository(fixture)
    current = repository.load_operation(fixture.operation_ref)
    assert current is not None
    transition = _preflight_transition(fixture, current)
    work = _submission_work(fixture)

    assert repository.atomic_terminal_and_quota(work, transition) == transition.operation
    assert repository.atomic_terminal_and_quota(work, transition) == transition.operation

    with psycopg.connect(fixture.dsn) as connection:
        operation_row = connection.execute(
            "SELECT send_state,send_state_version FROM "
            "platform.collection_submission_operation WHERE id=%s",
            (fixture.operation_id,),
        ).fetchone()
        dispatch_count = connection.execute(
            "SELECT count(*) FROM platform.collection_submission_dispatch_v2 WHERE operation_id=%s",
            (fixture.operation_id,),
        ).fetchone()
        lease_rows = connection.execute(
            "SELECT lease_state,released_at FROM platform.resource_lease "
            "WHERE operation_id=%s ORDER BY pub_id",
            (fixture.operation_id,),
        ).fetchall()
        reservation_row = connection.execute(
            "SELECT reservation_state FROM platform.collection_quota_reservation WHERE pub_id=%s",
            (fixture.reservation_pub_id,),
        ).fetchone()
        outbox_row = connection.execute(
            "SELECT event_key,payload_hash,publish_state FROM "
            "platform.collection_governance_outbox_v2 WHERE event_key=%s",
            (transition.outbox.outbox_key,),
        ).fetchone()

    assert operation_row == ("CONFIRMED_NOT_SENT", 2)
    assert dispatch_count == (0,)
    assert len(lease_rows) == 4
    assert all(row[0] == "released" and row[1] is not None for row in lease_rows)
    assert reservation_row == ("released",)
    assert outbox_row == (
        transition.outbox.outbox_key,
        transition.outbox.payload_sha256,
        "pending",
    )


def test_repository_capture_link_fact_and_provenance_round_trip_in_real_postgres() -> None:
    fixture = _seed_submission_fixture(_test_dsn())
    assert _claim_once(fixture, Barrier(1))
    repository = _worker_repository(fixture)
    current = repository.load_operation(fixture.operation_ref)
    assert current is not None
    transition = _submitted_transition(fixture, current)
    work = _submission_work(fixture)
    repository.atomic_terminal_and_quota(work, transition)

    capture = repository.load_capture(fixture.operation_ref)
    assert capture is not None
    assert capture.capture_state is CaptureState.NOT_STARTED
    admission = repository.resolve_capture_admission(
        work=work,
        operation=transition.operation,
        capture=capture,
    )
    assert admission.decision is CaptureAdmissionDecision.DIRECT_OWNER_LIVE
    requested_at = max(datetime.now(UTC), capture.updated_at + timedelta(microseconds=1))
    attempt = repository.start_or_resume_capture_attempt(
        work=work,
        context=_capture_context(fixture),
        capture=capture,
        requested_at=requested_at,
    )
    assert attempt.freshly_started
    replay = repository.start_or_resume_capture_attempt(
        work=work,
        context=_capture_context(fixture),
        capture=attempt.capture,
        requested_at=requested_at + timedelta(seconds=1),
    )
    assert replay.command == attempt.command
    assert not replay.freshly_started

    observed_at = max(datetime.now(UTC), attempt.command.requested_at) + timedelta(milliseconds=1)
    provenance = CaptureProvenance(
        capture_channel=CaptureChannel.WEB_DOM,
        capture_protocol_revision="capture-protocol-v1",
        observed_product_version="web-product-v1",
        capture_adapter_revision="capture-adapter-v1",
        data_classification=CaptureDataClassification.CUSTOMER_PRIVATE,
        dlp_policy_revision="dlp-policy-v1",
        retention_until=observed_at + timedelta(days=1),
    )
    raw = CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref=attempt.command.attempt_ref,
        evidence_ref=f"capture-evidence-{fixture.operation_id.hex[:16]}",
        evidence_sha256=_hash(f"capture-evidence-{fixture.operation_id}"),
        observed_at=observed_at,
        observed_surface_product=fixture.surface_product,
        provenance=provenance,
        staging=CaptureStagingRef(
            staging_key=attempt.command.staging_intent.staging_key,
            object_ref=attempt.command.staging_intent.object_ref,
            content_sha256=_hash(f"capture-content-{fixture.operation_id}"),
            byte_size=321,
            media_type="application/json",
            capture_schema_revision="capture-schema-v1",
            staged_at=observed_at + timedelta(milliseconds=1),
        ),
    )
    normalized = normalize_capture(attempt.command, raw)
    resolved = repository.resolve_capture_attempt(
        attempt=attempt,
        raw=raw,
        normalized=normalized,
    )

    assert resolved.capture_state is CaptureState.COMPLETED
    assert resolved.provenance == provenance
    assert resolved.staging == raw.staging
    assert resolved.staging is not None
    linked_at = max(datetime.now(UTC), resolved.updated_at, resolved.staging.staged_at)
    link = link_immutable_capture(
        resolved,
        linked_at=linked_at + timedelta(milliseconds=1),
    )
    assert repository.store_capture_link(link) == link
    assert repository.store_capture_link(link) == link
    assert repository.load_capture_link(fixture.operation_ref) == link

    recorded_at = link.linked_at + timedelta(milliseconds=1)
    fact = SlotOutcomeFact(
        operation=fixture.operation_ref,
        outcome=derive_slot_outcome(transition.operation, capture=resolved),
        operation_state_version=transition.operation.state_version,
        capture_state_version=resolved.state_version,
        capture_link_key=link.capture_link_key,
        is_final_primary=True,
        fact_version=1,
        recorded_at=recorded_at,
    )
    assert fact.outcome is SlotOutcome.CONFIRMED_SENT_CAPTURE_COMPLETE
    fact_payload_hash = sha256(canonical_json(fact).encode()).hexdigest()
    fact_event = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type="collection.slot.outcome",
            aggregate_ref=fixture.operation_ref.operation_pub_id,
            aggregate_version=fact.fact_version,
            payload_sha256=fact_payload_hash,
        ),
        event_type="collection.slot.outcome",
        aggregate_ref=fixture.operation_ref.operation_pub_id,
        aggregate_version=fact.fact_version,
        payload_sha256=fact_payload_hash,
        occurred_at=fact.recorded_at,
    )
    assert repository.atomic_fact_and_outbox(work, fact, fact_event) == fact
    assert repository.atomic_fact_and_outbox(work, fact, fact_event) == fact
    assert repository.load_fact(fixture.operation_ref) == fact
    assert fact_event in repository.pending_outbox(fixture.operation_ref)

    with psycopg.connect(fixture.dsn) as connection:
        durable = connection.execute(
            """
            SELECT truth.active_command_json,manifest.capture_channel,
                   manifest.capture_protocol_revision,
                   manifest.observed_product_version,
                   manifest.capture_adapter_revision,
                   manifest.data_classification,manifest.dlp_policy_revision,
                   manifest.retention_until
              FROM platform.collection_capture_truth_v2 AS truth
              JOIN platform.collection_capture_manifest_v2 AS manifest
                ON manifest.id=truth.current_capture_manifest_id
             WHERE truth.operation_id=%s
            """,
            (fixture.operation_id,),
        ).fetchone()
        linked_fact = connection.execute(
            """
            SELECT manifest.capture_link_key,manifest.storage_state,
                   outcome.outcome_state,outcome.fact_version,
                   outcome.capture_link_key,outbox.event_key,outbox.publish_state
              FROM platform.collection_capture_truth_v2 AS truth
              JOIN platform.collection_capture_manifest_v2 AS manifest
                ON manifest.id=truth.current_capture_manifest_id
              JOIN platform.collection_slot_outcome_v2 AS outcome
                ON outcome.operation_id=truth.operation_id
               AND outcome.fact_version=1
              JOIN platform.collection_governance_outbox_v2 AS outbox
                ON outbox.operation_id=truth.operation_id
               AND outbox.event_key=%s
             WHERE truth.operation_id=%s
            """,
            (fact_event.outbox_key, fixture.operation_id),
        ).fetchone()
        analysis_admission_count = connection.execute(
            "SELECT count(*) FROM platform.collection_analysis_admission_v2 WHERE operation_id=%s",
            (fixture.operation_id,),
        ).fetchone()
    assert durable == (
        canonical_json(attempt.command),
        provenance.capture_channel.value,
        provenance.capture_protocol_revision,
        provenance.observed_product_version,
        provenance.capture_adapter_revision,
        provenance.data_classification.value,
        provenance.dlp_policy_revision,
        provenance.retention_until,
    )
    assert linked_fact == (
        link.capture_link_key,
        "linked",
        fact.outcome.value,
        fact.fact_version,
        link.capture_link_key,
        fact_event.outbox_key,
        "pending",
    )
    assert analysis_admission_count == (0,)


def test_repository_surface_mismatch_persists_stable_reason_and_fact_in_real_postgres() -> None:
    fixture = _seed_submission_fixture(_test_dsn())
    assert _claim_once(fixture, Barrier(1))
    repository = _worker_repository(fixture)
    current = repository.load_operation(fixture.operation_ref)
    assert current is not None
    transition = _submitted_transition(fixture, current)
    work = _submission_work(fixture)
    repository.atomic_terminal_and_quota(work, transition)

    capture = repository.load_capture(fixture.operation_ref)
    assert capture is not None and capture.capture_state is CaptureState.NOT_STARTED
    requested_at = max(datetime.now(UTC), capture.updated_at + timedelta(microseconds=1))
    attempt = repository.start_or_resume_capture_attempt(
        work=work,
        context=_capture_context(fixture),
        capture=capture,
        requested_at=requested_at,
    )
    observed_at = max(datetime.now(UTC), attempt.command.requested_at) + timedelta(milliseconds=1)
    observed_product = SurfaceProductRef(
        platform=fixture.surface_product.platform,
        collection_surface=fixture.surface_product.collection_surface,
        product_variant=f"{fixture.surface_product.product_variant}-mismatch",
        target_key=(
            f"collection-target-v1|platform={fixture.surface_product.platform}|"
            f"collection_surface={fixture.surface_product.collection_surface.value}|"
            f"product_variant={fixture.surface_product.product_variant}-mismatch"
        ),
    )
    raw = CaptureDisposition(
        capture_state=CaptureState.COMPLETED,
        attempt_ref=attempt.command.attempt_ref,
        evidence_ref=f"capture-mismatch-{fixture.operation_id.hex[:16]}",
        evidence_sha256=_hash(f"capture-mismatch-{fixture.operation_id}"),
        observed_at=observed_at,
        observed_surface_product=observed_product,
        provenance=CaptureProvenance(
            capture_channel=CaptureChannel.WEB_DOM,
            capture_protocol_revision="capture-protocol-v1",
            observed_product_version="mismatched-web-product-v1",
            capture_adapter_revision="capture-adapter-v1",
            data_classification=CaptureDataClassification.CUSTOMER_PRIVATE,
            dlp_policy_revision="dlp-policy-v1",
            retention_until=observed_at + timedelta(days=1),
        ),
        staging=CaptureStagingRef(
            staging_key=attempt.command.staging_intent.staging_key,
            object_ref=attempt.command.staging_intent.object_ref,
            content_sha256=_hash(f"capture-mismatch-content-{fixture.operation_id}"),
            byte_size=111,
            media_type="application/json",
            capture_schema_revision="capture-schema-v1",
            staged_at=observed_at + timedelta(milliseconds=1),
        ),
    )
    normalized = normalize_capture(attempt.command, raw)
    assert normalized.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    resolved = repository.resolve_capture_attempt(
        attempt=attempt,
        raw=raw,
        normalized=normalized,
    )
    assert resolved.capture_state is CaptureState.NOT_OBSERVABLE
    assert resolved.normalization is CaptureNormalizationDecision.QUARANTINED_SURFACE_MISMATCH
    assert repository.load_capture(fixture.operation_ref) == resolved

    assert raw.staging is not None
    fact = SlotOutcomeFact(
        operation=fixture.operation_ref,
        outcome=derive_slot_outcome(transition.operation, capture=resolved),
        operation_state_version=transition.operation.state_version,
        capture_state_version=resolved.state_version,
        is_final_primary=False,
        fact_version=1,
        recorded_at=raw.staging.staged_at + timedelta(milliseconds=1),
    )
    assert fact.outcome is SlotOutcome.INVALID_SURFACE_OR_PRODUCT
    fact_hash = sha256(canonical_json(fact).encode()).hexdigest()
    event = OutboxEventRef(
        outbox_key=deterministic_outbox_key(
            event_type="collection.slot.outcome",
            aggregate_ref=fixture.operation_ref.operation_pub_id,
            aggregate_version=fact.fact_version,
            payload_sha256=fact_hash,
        ),
        event_type="collection.slot.outcome",
        aggregate_ref=fixture.operation_ref.operation_pub_id,
        aggregate_version=fact.fact_version,
        payload_sha256=fact_hash,
        occurred_at=fact.recorded_at,
    )
    assert repository.atomic_fact_and_outbox(work, fact, event) == fact

    with psycopg.connect(fixture.dsn) as connection:
        persisted = connection.execute(
            """
            SELECT manifest.reason_code,manifest.storage_state,
                   manifest.capture_state,manifest.observed_product_variant,
                   outcome.outcome_state,outcome.capture_state_version
              FROM platform.collection_capture_truth_v2 AS truth
              JOIN platform.collection_capture_manifest_v2 AS manifest
                ON manifest.id=truth.current_capture_manifest_id
              JOIN platform.collection_slot_outcome_v2 AS outcome
                ON outcome.operation_id=truth.operation_id
               AND outcome.fact_version=1
             WHERE truth.operation_id=%s
            """,
            (fixture.operation_id,),
        ).fetchone()
    assert persisted == (
        "invalid_surface_or_product",
        "quarantined",
        CaptureState.NOT_OBSERVABLE.value,
        observed_product.product_variant,
        SlotOutcome.INVALID_SURFACE_OR_PRODUCT.value,
        resolved.state_version,
    )


def test_rls_cross_tenant_and_public_api_function_execution_are_denied(
    submission_fixture: _SubmissionFixture,
) -> None:
    repository = _worker_repository(submission_fixture)
    assert repository.missing_function_contracts() == ()
    capabilities = repository.capabilities()
    assert capabilities.atomic_terminal_and_quota
    assert capabilities.terminal_replay_integrity
    assert capabilities.durable_capture_admission
    assert capabilities.immutable_capture_link
    assert capabilities.atomic_fact_and_outbox
    assert not capabilities.durable_analysis_command

    other_tenant = uuid4()
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, other_tenant, role="geo_worker")
            hidden = connection.execute(
                "SELECT id FROM platform.collection_submission_operation WHERE id=%s",
                (submission_fixture.operation_id,),
            ).fetchone()
    assert hidden is None

    with psycopg.connect(submission_fixture.dsn) as connection:
        result_shapes = connection.execute(
            """
            SELECT requested.signature,
                   pg_get_function_result(to_regprocedure(requested.signature))
              FROM unnest(%s::text[]) AS requested(signature)
             ORDER BY requested.signature
            """,
            ([contract.regprocedure for contract in S10_FUNCTION_CONTRACTS],),
        ).fetchall()
        expected_shapes = sorted(
            (contract.regprocedure, contract.database_result) for contract in S10_FUNCTION_CONTRACTS
        )
        assert result_shapes == expected_shapes
        acl_rows = connection.execute(
            """
            SELECT signature,
                   NOT EXISTS (
                     SELECT 1
                       FROM aclexplode(
                         COALESCE(procedure.proacl,
                                  acldefault('f',procedure.proowner))
                       ) AS acl
                      WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
                   ) AS public_denied,
                   NOT has_function_privilege('geo_api',procedure.oid,'EXECUTE')
              FROM unnest(%s::text[]) AS requested(signature)
              JOIN pg_proc AS procedure
                ON procedure.oid=to_regprocedure(requested.signature)
             ORDER BY signature
            """,
            (list(RESTRICTED_SIGNATURES),),
        ).fetchall()
    assert len(acl_rows) == len(RESTRICTED_SIGNATURES)
    assert all(bool(row[1]) and bool(row[2]) for row in acl_rows)

    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_api")
            current_role = connection.execute("SELECT current_user").fetchone()
            assert current_role == ("geo_api",)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(PREPARE_SQL, submission_fixture.prepare_args).fetchone()


def test_worker_operation_table_is_read_only_and_admission_is_tenant_bound(
    submission_fixture: _SubmissionFixture,
) -> None:
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
            privileges = connection.execute(
                """
                SELECT has_table_privilege('geo_worker',
                           'platform.collection_submission_operation','SELECT'),
                       has_table_privilege('geo_worker',
                           'platform.collection_submission_operation','INSERT'),
                       has_table_privilege('geo_worker',
                           'platform.collection_submission_operation','UPDATE'),
                       has_table_privilege('geo_worker',
                           'platform.collection_submission_operation','DELETE')
                """
            ).fetchone()
            assert privileges == (True, False, False, False)
            replay = connection.execute(
                CREATE_OPERATION_SQL,
                submission_fixture.create_operation_args,
            ).fetchone()
            assert replay == (submission_fixture.operation_id, False)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO platform.collection_submission_operation (id) VALUES (%s)",
                        (uuid4(),),
                    )

    cross_tenant_args = list(submission_fixture.create_operation_args)
    cross_tenant_args[0] = uuid4()
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
            with pytest.raises(psycopg.Error, match="tenant context mismatch"):
                connection.execute(CREATE_OPERATION_SQL, tuple(cross_tenant_args)).fetchone()

    unused_args = _unused_primary_admission_args(submission_fixture)
    drifted_key_args = list(unused_args)
    drifted_key_args[4] = f"{unused_args[4]}-drift"
    with psycopg.connect(submission_fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
            with pytest.raises(psycopg.Error, match="key is not deterministic"):
                connection.execute(CREATE_OPERATION_SQL, tuple(drifted_key_args)).fetchone()

    with pytest.raises(
        psycopg.Error,
        match="preparation must complete in one transaction",
    ):
        with psycopg.connect(submission_fixture.dsn) as connection:
            with connection.transaction():
                _set_scope(connection, submission_fixture.tenant_id, role="geo_worker")
                created = connection.execute(CREATE_OPERATION_SQL, unused_args).fetchone()
                assert created is not None and bool(created[1])

    with psycopg.connect(submission_fixture.dsn) as connection:
        persisted = connection.execute(
            "SELECT count(*) FROM platform.collection_submission_operation WHERE pub_id=%s",
            (unused_args[2],),
        ).fetchone()
    assert persisted == (0,)
