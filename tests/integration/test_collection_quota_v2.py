"""Stage-2 PostgreSQL lock and runtime-role tests for collection-v2 quota.

These tests only run against an explicitly configured local/isolated database.
They create durable submission operations but never contact a provider or perform
an external send.  The suite intentionally targets the ``s07_0002`` ACL boundary:
Stage 3 replaces its direct worker DML with restricted s10 repository entrypoints.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from typing import cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg
import pytest
from geo_platform.collection.campaign_materialization_v2 import CampaignMaterializationV2
from geo_platform.collection.identity_v2 import (
    CampaignActors,
    CampaignFreezeRequest,
    ConfigFreezeRequest,
    QuestionSlotRef,
    freeze_campaign,
    freeze_config,
)
from geo_platform.collection.models import (
    CollectionConfigRevisionV2,
    CollectionConfigTargetV2,
)
from geo_platform.collection.quota_v2 import (
    ConnectionProtocol,
    OwnerEvidence,
    QuotaV2Error,
    ReconcileQuotaRequest,
    ReconciliationAction,
    ReservationDisposition,
    ReserveQuotaRequest,
    ReserveQuotaResult,
    SettlementResult,
    SettleQuotaRequest,
    materialize_quota_buckets,
    reconcile_quota,
    reserve_quota,
    settle_quota,
)
from geo_platform.tenancy.repository import set_tenant_context
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domain.collection.surface import (
    QUOTA_SCOPE_KIND_LOCK_ORDER,
    CapabilityDeclaration,
    CapabilityRegistry,
    CapabilityStatus,
    CollectionConfigV2,
    CollectionSurface,
    CollectionTarget,
    QuotaScopeDeclaration,
    QuotaScopeKind,
    QuotaWindowPolicy,
    QuotaWindowUnit,
    SendState,
)

pytestmark = pytest.mark.compat_postgres


@dataclass(frozen=True, slots=True)
class _ServiceFixture:
    tenant_id: UUID
    project_id: UUID
    binding_id: UUID
    registry_id: UUID
    resource_id: UUID
    resource_pub_id: str
    operation_ids: tuple[UUID, UUID]
    bucket_keys: tuple[str, ...]


def _test_dsn() -> str:
    dsn = os.getenv("COLLECTION_QUOTA_V2_TEST_DSN")
    if not dsn:
        pytest.fail("COLLECTION_QUOTA_V2_TEST_DSN is not configured")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("quota integration tests refuse non-loopback PostgreSQL")
    if os.getenv("COLLECTION_QUOTA_V2_TEST_AS_WORKER") != "1":
        pytest.fail("Stage-2 quota integration requires the geo_worker role gate")
    with psycopg.connect(dsn) as connection:
        revisions = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ).fetchall()
        )
    if revisions != ("s07_0002_execution_governance",):
        pytest.fail(
            "Stage-2 direct-DML quota integration requires exact s07_0002; "
            "use the restricted repository suite at s10 or later"
        )
    return dsn


def _pub(prefix: str, value: UUID) -> str:
    return f"{prefix}_{value.hex[:26]}"


def _scope(kind: QuotaScopeKind, *, limit: int) -> QuotaScopeDeclaration:
    return QuotaScopeDeclaration(
        policy_revision="quota-integration-v1",
        scope_kind=kind,
        scope_subject_id=f"integration-{kind.value}-{uuid4().hex[:8]}",
        limit=limit,
        window=QuotaWindowPolicy(
            unit=QuotaWindowUnit.DAY,
            timezone="UTC",
            boundary_revision="calendar-v1",
        ),
        interaction_mode="search" if kind is QuotaScopeKind.MODE else None,
    )


def _ensure_migration(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    exists = connection.execute("SELECT to_regclass('platform.collection_quota_bucket')").fetchone()
    if exists is None or exists[0] is None:
        pytest.fail("s07_0002 quota migration is not installed")


def _activate_worker_role(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("SET ROLE geo_worker")
    connection.commit()


def _sqlalchemy_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


def _seed_frozen_campaign(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    token: str,
) -> tuple[UUID, tuple[tuple[object, ...], ...]]:
    occurred_at = datetime(2026, 8, 24, 12, tzinfo=UTC)
    capability_revision = f"doubao-web-search-{token}"
    target = CollectionTarget(
        platform="doubao",
        collection_surface=CollectionSurface.CONSUMER_WEB,
        product_variant="chat",
        interaction_modes=("search",),
    )
    config = CollectionConfigV2(
        question_set_revision=f"questions-{token}",
        collection_targets=(target,),
        province_codes=("110000",),
        samples_per_cell=2,
        schedule_policy={},
        comparison_policy_revision=f"comparison-{token}",
    )
    capability_registry = CapabilityRegistry(
        registry_revision=f"capabilities-{token}",
        capabilities=(
            CapabilityDeclaration(
                capability_revision=capability_revision,
                platform="doubao",
                collection_surface=CollectionSurface.CONSUMER_WEB,
                product_variant="chat",
                interaction_mode="search",
                status=CapabilityStatus.SUPPORTED,
                production_allowed=True,
            ),
        ),
    )
    frozen_config = freeze_config(
        ConfigFreezeRequest(
            revision_pub_id=f"ccr2_q_{token}",
            tenant_id=tenant_id,
            project_id=project_id,
            revision=1,
            config=config,
            capability_registry=capability_registry,
            change_reason="quota-integration",
            approved_by_pub_id="quota-integration-reviewer",
            frozen_at=occurred_at,
        )
    )
    blueprint = freeze_campaign(
        CampaignFreezeRequest(
            campaign_pub_id=f"cmp_q_{token}",
            tenant_id=tenant_id,
            project_id=project_id,
            config_revision=frozen_config,
            question_slots=(
                QuestionSlotRef(
                    question_slot_id=f"question-{token}",
                    question_revision=f"question-revision-{token}",
                ),
            ),
            time_window_key="2026-08-24/2026-08-25",
            run_trigger_source="manual",
            trigger_idempotency_key=f"campaign-{token}",
            actors=CampaignActors(
                created_by_pub_id="quota-integration",
                approved_by_pub_id="quota-integration-reviewer",
                triggered_by_pub_id="quota-integration",
            ),
            binding_policy_revision=f"binding-policy-{token}",
            frozen_at=occurred_at,
        )
    )

    engine = create_engine(_sqlalchemy_dsn(dsn))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        plan = frozen_config.persistence_plan
        with factory() as session:
            with session.begin():
                set_tenant_context(
                    session,
                    tenant_id=tenant_id,
                    tenant_pub_id=_pub("tnt", tenant_id),
                )
                session.add(CollectionConfigRevisionV2(**plan.parent_insert_values))
                session.add_all(
                    CollectionConfigTargetV2(**values) for values in plan.target_insert_values
                )
                session.flush()
                config_row = session.get(
                    CollectionConfigRevisionV2,
                    frozen_config.id,
                    with_for_update=True,
                )
                assert config_row is not None
                config_row.lifecycle_state = "frozen"
                config_row.frozen_at = frozen_config.frozen_at
            with session.begin():
                set_tenant_context(
                    session,
                    tenant_id=tenant_id,
                    tenant_pub_id=_pub("tnt", tenant_id),
                )
                config_row = session.get(
                    CollectionConfigRevisionV2,
                    frozen_config.id,
                    with_for_update=True,
                )
                assert config_row is not None
                config_row.lifecycle_state = "active"
                config_row.activated_at = occurred_at + timedelta(seconds=1)

        service = CampaignMaterializationV2(
            session_factory=factory,
            tenant_pub_id=_pub("tnt", tenant_id),
        )
        confirmation = service.materialize_and_freeze(blueprint, chunk_size=1)
        assert confirmation.state == "frozen"
    finally:
        engine.dispose()

    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        slots = connection.execute(
            "SELECT id,campaign_target_id,sampling_leg_id,slot_key,platform,"
            "collection_surface,product_variant,province_code,interaction_mode "
            "FROM platform.collection_primary_slot "
            "WHERE tenant_id=%s AND project_id=%s AND campaign_id=%s "
            "ORDER BY slot_ordinal",
            (tenant_id, project_id, blueprint.id),
        ).fetchall()
    assert len(slots) == 2
    return (blueprint.id, tuple(tuple(row) for row in slots))


def _seed_service_fixture(dsn: str) -> _ServiceFixture:
    token = uuid4().hex[:8]
    tenant_id, customer_id, project_id = (uuid4() for _ in range(3))
    tenant_pub_id = _pub("tnt", tenant_id)
    now = datetime.now(UTC)

    with psycopg.connect(dsn) as connection:
        _ensure_migration(connection)
        connection.execute(
            "INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at) "
            "VALUES (%s,%s,'quota service integration','active',now(),now())",
            (tenant_id, tenant_pub_id),
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            "INSERT INTO platform.customer "
            "(id,pub_id,tenant_id,version,created_at,updated_at,name) "
            "VALUES (%s,%s,%s,1,now(),now(),'quota service integration')",
            (customer_id, _pub("cus", customer_id), tenant_id),
        )
        connection.execute(
            "INSERT INTO platform.project "
            "(id,pub_id,tenant_id,version,created_at,updated_at,customer_id,name,state) "
            "VALUES (%s,%s,%s,1,now(),now(),%s,'quota service integration','active')",
            (project_id, _pub("prj", project_id), tenant_id, customer_id),
        )

    campaign_id, slots = _seed_frozen_campaign(
        dsn,
        tenant_id=tenant_id,
        project_id=project_id,
        token=token,
    )

    capability_registry_id, capability_id = uuid4(), uuid4()
    capability_revision = f"doubao-web-search-{token}"
    capability_registry_revision = f"capabilities-{token}"
    quota_registry_id = uuid4()
    quota_registry_revision = f"quota-{token}"
    scopes = (
        _scope(QuotaScopeKind.PROVIDER, limit=2),
        _scope(QuotaScopeKind.MODE, limit=1),
    )
    materialized = materialize_quota_buckets(
        tenant_id=tenant_id,
        project_id=project_id,
        scopes=scopes,
        occurred_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    binding_id = uuid4()
    browser_owner_id = uuid4()
    browser_owner_pub_id = _pub("rrg", browser_owner_id)
    resource_specs = (
        (
            "governed_account",
            uuid4(),
            f"account-{token}",
            f"governed-account-{token}",
        ),
        (
            "browser_owner",
            browser_owner_id,
            browser_owner_pub_id,
            f"browser-owner-{token}",
        ),
        (
            "browser_profile",
            uuid4(),
            f"browser-profile-{token}",
            f"browser-profile-{token}",
        ),
        (
            "web_session",
            uuid4(),
            f"web-session-{token}",
            f"web-session-{token}",
        ),
    )
    operation_ids = (uuid4(), uuid4())

    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO platform.collection_capability_registry_revision
              (id,pub_id,tenant_id,project_id,schema_version,registry_revision,
               lifecycle_state,canonical_json,revision_hash,change_reason)
            VALUES
              (%s,%s,%s,%s,'collection-capability-registry-v1',%s,
               'candidate','{}',%s,'quota_service_integration')
            """,
            (
                capability_registry_id,
                _pub("crr", capability_registry_id),
                tenant_id,
                project_id,
                capability_registry_revision,
                sha256(b"{}").hexdigest(),
            ),
        )
        connection.execute(
            """
            INSERT INTO platform.collection_capability_declaration
              (id,pub_id,tenant_id,project_id,registry_revision_id,schema_version,
               declaration_key,capability_revision,platform,collection_surface,
               product_variant,interaction_mode,status,production_allowed,
               required_resource_kinds_json,observable_capture_fields_json,
               product_version_constraints_json)
            VALUES
              (%s,%s,%s,%s,%s,'collection-capability-v1',%s,%s,'doubao',
               'consumer_web','chat','search','supported',true,
               '["governed_account","browser_owner","browser_profile",'
               '"web_session"]','["answer_text"]','{}')
            """,
            (
                capability_id,
                _pub("cap", capability_id),
                tenant_id,
                project_id,
                capability_registry_id,
                f"capability|doubao|consumer_web|chat|search|{token}",
                capability_revision,
            ),
        )
        connection.execute(
            "UPDATE platform.collection_capability_registry_revision "
            "SET lifecycle_state='frozen',frozen_at=%s WHERE id=%s",
            (now, capability_registry_id),
        )
        connection.execute(
            "UPDATE platform.collection_capability_registry_revision "
            "SET lifecycle_state='active',activated_at=%s WHERE id=%s",
            (now, capability_registry_id),
        )

        connection.execute(
            """
            INSERT INTO platform.collection_quota_registry_revision
              (id,pub_id,tenant_id,project_id,schema_version,registry_revision,
               lock_order_version,lifecycle_state,canonical_json,revision_hash,
               change_reason)
            VALUES
              (%s,%s,%s,%s,'quota-scope-registry-v1',%s,
               'quota-scope-lock-order-v1','candidate','{}',%s,
               'quota_service_integration')
            """,
            (
                quota_registry_id,
                _pub("qrr", quota_registry_id),
                tenant_id,
                project_id,
                quota_registry_revision,
                sha256(b"{}").hexdigest(),
            ),
        )
        policy_ids: list[UUID] = []
        for scope in scopes:
            policy_id = uuid4()
            policy_ids.append(policy_id)
            connection.execute(
                """
                INSERT INTO platform.collection_quota_scope_policy
                  (id,pub_id,tenant_id,project_id,registry_revision_id,schema_version,
                   scope_policy_key,selector_key,policy_revision,scope_kind,
                   scope_subject_id,platform,collection_surface,product_variant,
                   interaction_mode,share_policy,window_schema_version,window_unit,
                   window_size,window_timezone,window_boundary_revision,
                   provider_window_code,limit_units,limit_source,
                   settlement_policy_revision,lock_order_ordinal)
                VALUES
                  (%s,%s,%s,%s,%s,'quota-scope-v1',%s,%s,%s,%s,%s,%s,%s,%s,
                   %s,'shared','quota-window-v1',%s,%s,%s,%s,%s,%s,
                   'project_policy','settlement-v1',%s)
                """,
                (
                    policy_id,
                    _pub("qsp", policy_id),
                    tenant_id,
                    project_id,
                    quota_registry_id,
                    scope.scope_key,
                    scope.selector_key,
                    scope.policy_revision,
                    scope.scope_kind.value,
                    scope.scope_subject_id,
                    scope.platform,
                    scope.collection_surface.value if scope.collection_surface else None,
                    scope.product_variant,
                    scope.interaction_mode,
                    scope.window.unit.value,
                    scope.window.size,
                    scope.window.timezone,
                    scope.window.boundary_revision,
                    scope.window.provider_window_code,
                    scope.limit,
                    QUOTA_SCOPE_KIND_LOCK_ORDER.index(scope.scope_kind),
                ),
            )
        connection.execute(
            "UPDATE platform.collection_quota_registry_revision "
            "SET lifecycle_state='frozen',frozen_at=%s WHERE id=%s",
            (now, quota_registry_id),
        )
        connection.execute(
            "UPDATE platform.collection_quota_registry_revision "
            "SET lifecycle_state='active',activated_at=%s WHERE id=%s",
            (now, quota_registry_id),
        )

        for resource_kind, resource_id, resource_pub_id, owner_handle in resource_specs:
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
                  (%s,%s,%s,1,now(),now(),%s,%s,'{}','cn',1,'active',now(),%s,
                   'collection-resource-v2',%s,'resident_browser',
                   'browser-owner-v1',%s,'attestation-v1','route-v1',%s,%s)
                """,
                (
                    resource_id,
                    resource_pub_id,
                    tenant_id,
                    resource_kind,
                    f"quota-{resource_kind}",
                    project_id,
                    f"{resource_kind}-{token}",
                    owner_handle,
                    sha256(f"{resource_kind}-{token}".encode()).hexdigest(),
                    now,
                ),
            )

        connection.execute(
            """
            INSERT INTO platform.collection_binding_revision_v2
              (id,pub_id,tenant_id,project_id,schema_version,binding_key,
               binding_revision,binding_policy_revision,lifecycle_state,
               lifecycle_reason,platform,collection_surface,product_variant,
               capability_registry_id,capability_registry_revision,
               quota_registry_id,quota_registry_revision,quota_policy_revision,
               region_policy_revision,route_policy_revision,resource_policy_revision,
               readiness_revision,required_resource_kinds_json,
               credential_references_json,canonical_json,binding_hash,owner_pub_id,
               approved_by_pub_id,approved_at,effective_from,expires_at)
            VALUES
              (%s,%s,%s,%s,'collection-binding-v1',%s,1,%s,'candidate',
               'quota_service_integration','doubao','consumer_web','chat',
               %s,%s,%s,%s,%s,'region-v1','route-v1','resource-v1','ready-v1',
               '["governed_account","browser_owner","browser_profile",'
               '"web_session"]','{}','{}',%s,'quota-integration-owner',
               'quota-integration-reviewer',%s,%s,%s)
            """,
            (
                binding_id,
                _pub("bnd", binding_id),
                tenant_id,
                project_id,
                f"binding|doubao|consumer_web|chat|{token}",
                f"binding-policy-{token}",
                capability_registry_id,
                capability_registry_revision,
                quota_registry_id,
                quota_registry_revision,
                scopes[0].policy_revision,
                sha256(b"{}").hexdigest(),
                now,
                now - timedelta(days=1),
                now + timedelta(days=1),
            ),
        )
        subtype_id = uuid4()
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
                subtype_id,
                _pub("wbd", subtype_id),
                tenant_id,
                project_id,
                binding_id,
                f"account-{token}",
                f"browser-owner-{token}",
                f"browser-profile-{token}",
                f"web-session-{token}",
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
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,'doubao','consumer_web','chat','search',
               'required',0)
            """,
            (
                capability_mapping_id,
                _pub("bcp", capability_mapping_id),
                tenant_id,
                project_id,
                binding_id,
                capability_id,
                capability_revision,
            ),
        )
        for resource_kind, resource_id, resource_pub_id, _owner_handle in resource_specs:
            resource_mapping_id = uuid4()
            connection.execute(
                """
                INSERT INTO platform.collection_binding_resource
                  (id,pub_id,tenant_id,project_id,binding_revision_id,
                   resource_registration_id,resource_pub_id,resource_kind,
                   resource_role,ordinal,required,adoption_required,mapping_revision)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,true,false,'mapping-v1')
                """,
                (
                    resource_mapping_id,
                    _pub("brs", resource_mapping_id),
                    tenant_id,
                    project_id,
                    binding_id,
                    resource_id,
                    resource_pub_id,
                    resource_kind,
                    resource_kind,
                ),
            )
        for ordinal, (scope, policy_id) in enumerate(zip(scopes, policy_ids, strict=True)):
            mapping_id = uuid4()
            connection.execute(
                """
                INSERT INTO platform.collection_binding_quota_scope
                  (id,pub_id,tenant_id,project_id,binding_revision_id,
                   quota_scope_policy_id,quota_registry_id,scope_policy_key,
                   scope_kind,scope_subject_id,policy_revision,applicability_key,
                   quota_units,ordinal)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
                """,
                (
                    mapping_id,
                    _pub("bqs", mapping_id),
                    tenant_id,
                    project_id,
                    binding_id,
                    policy_id,
                    quota_registry_id,
                    scope.scope_key,
                    scope.scope_kind.value,
                    scope.scope_subject_id,
                    scope.policy_revision,
                    f"all-operations-{ordinal}",
                    ordinal,
                ),
            )
        connection.execute(
            "UPDATE platform.collection_binding_revision_v2 "
            "SET lifecycle_state='active',activated_at=%s WHERE id=%s",
            (now, binding_id),
        )

        for ordinal, (operation_id, slot) in enumerate(
            zip(operation_ids, slots, strict=True),
            start=1,
        ):
            (
                slot_id,
                campaign_target_id,
                sampling_leg_id,
                slot_key,
                platform,
                surface,
                product,
                province,
                mode,
            ) = slot
            connection.execute(
                """
                INSERT INTO platform.collection_submission_operation
                  (id,pub_id,tenant_id,project_id,campaign_id,campaign_target_id,
                   sampling_leg_id,primary_slot_id,slot_key,platform,
                   collection_surface,product_variant,province_code,
                   interaction_mode,operation_generation,operation_key,
                   operation_policy_revision,send_state,send_state_version,
                   prepared_at,reconciliation_state,reconcile_after,state_reason)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,
                   'operation-policy-v1','NOT_SENT',1,%s,'not_required',%s,
                   'quota_service_integration')
                """,
                (
                    operation_id,
                    _pub("opr", operation_id),
                    tenant_id,
                    project_id,
                    campaign_id,
                    campaign_target_id,
                    sampling_leg_id,
                    slot_id,
                    slot_key,
                    platform,
                    surface,
                    product,
                    province,
                    mode,
                    f"operation-{token}-{ordinal}",
                    datetime(2026, 8, 24, 12, tzinfo=UTC),
                    datetime(2026, 8, 24, 13, tzinfo=UTC),
                ),
            )

    return _ServiceFixture(
        tenant_id=tenant_id,
        project_id=project_id,
        binding_id=binding_id,
        registry_id=quota_registry_id,
        resource_id=browser_owner_id,
        resource_pub_id=browser_owner_pub_id,
        operation_ids=operation_ids,
        bucket_keys=tuple(bucket.bucket_key for bucket in materialized),
    )


def _reserve_with_service(
    dsn: str,
    fixture: _ServiceFixture,
    operation_id: UUID,
    barrier: Barrier | None = None,
) -> ReserveQuotaResult:
    if barrier is not None:
        barrier.wait(timeout=10)
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        return reserve_quota(
            cast(ConnectionProtocol, connection),
            ReserveQuotaRequest(
                tenant_id=fixture.tenant_id,
                project_id=fixture.project_id,
                operation_id=operation_id,
                binding_id=fixture.binding_id,
                registry_id=fixture.registry_id,
                requested_units=1,
            ),
        )


def _settle_with_service(
    dsn: str,
    fixture: _ServiceFixture,
    operation_id: UUID,
) -> SettlementResult:
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        return settle_quota(
            cast(ConnectionProtocol, connection),
            SettleQuotaRequest(
                tenant_id=fixture.tenant_id,
                project_id=fixture.project_id,
                operation_id=operation_id,
                target_send_state=SendState.CONFIRMED_NOT_SENT,
                reason_code="integration_confirmed_not_sent",
            ),
        )


def _service_bucket_projection(
    dsn: str,
    fixture: _ServiceFixture,
) -> tuple[tuple[int, int, int], ...]:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        rows = connection.execute(
            "SELECT reserved_units,settled_consumed_units,settled_unknown_units "
            "FROM platform.collection_quota_bucket "
            "WHERE tenant_id=%s AND project_id=%s AND registry_revision_id=%s "
            "ORDER BY CASE scope_kind WHEN 'provider' THEN 0 WHEN 'mode' THEN 6 "
            "ELSE 2147483647 END,bucket_key",
            (fixture.tenant_id, fixture.project_id, fixture.registry_id),
        ).fetchall()
    return tuple((int(row[0]), int(row[1]), int(row[2])) for row in rows)


def _service_bucket_keys(dsn: str, fixture: _ServiceFixture) -> tuple[str, ...]:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        rows = connection.execute(
            "SELECT bucket_key FROM platform.collection_quota_bucket "
            "WHERE tenant_id=%s AND project_id=%s AND registry_revision_id=%s "
            "ORDER BY CASE scope_kind WHEN 'provider' THEN 0 WHEN 'mode' THEN 6 "
            "ELSE 2147483647 END,bucket_key",
            (fixture.tenant_id, fixture.project_id, fixture.registry_id),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _mark_operation_sending(
    dsn: str,
    fixture: _ServiceFixture,
    operation_id: UUID,
) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        transitioned = connection.execute(
            "UPDATE platform.collection_submission_operation "
            "SET send_state='SENDING',send_state_version=send_state_version+1,"
            "send_started_at=now(),reconciliation_state='pending',"
            "state_reason='integration_submit_started',version=version+1,"
            "updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s "
            "AND send_state='NOT_SENT' RETURNING id",
            (operation_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
        assert transitioned is not None


def _create_formal_lease(
    dsn: str,
    fixture: _ServiceFixture,
    operation_id: UUID,
) -> tuple[UUID, UUID]:
    capacity_id, lease_id = uuid4(), uuid4()
    acquired_at = datetime.now(UTC)
    heartbeat_at = acquired_at + timedelta(minutes=1)
    expires_at = heartbeat_at + timedelta(minutes=10)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        connection.execute(
            """
            INSERT INTO platform.collection_resource_capacity_unit
              (id,pub_id,tenant_id,project_id,resource_registration_id,
               resource_pub_id,resource_kind,capacity_unit_key,unit_ordinal,
               capacity_state,current_fencing_token,owner_gateway_revision,
               last_heartbeat_at,state_reason)
            VALUES
              (%s,%s,%s,%s,%s,%s,'browser_owner',%s,1,'candidate',0,
               'browser-owner-v1',%s,'integration_capacity_created')
            """,
            (
                capacity_id,
                _pub("rcu", capacity_id),
                fixture.tenant_id,
                fixture.project_id,
                fixture.resource_id,
                fixture.resource_pub_id,
                f"capacity-{capacity_id}",
                acquired_at,
            ),
        )
        connection.execute(
            "UPDATE platform.collection_resource_capacity_unit "
            "SET capacity_state='available',state_reason='integration_available',"
            "version=version+1,updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s",
            (capacity_id, fixture.tenant_id, fixture.project_id),
        )
        connection.execute(
            "UPDATE platform.collection_resource_capacity_unit "
            "SET capacity_state='leased',current_fencing_token=1,"
            "state_reason='integration_leased',version=version+1,updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s",
            (capacity_id, fixture.tenant_id, fixture.project_id),
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
            VALUES
              (%s,%s,%s,1,now(),now(),'browser_owner',%s,
               'quota-integration-worker','{}','cn',1,%s,NULL,%s,
               'collection-resource-lease-v2',%s,%s,%s,%s,%s,1,'active',
               %s,%s,NULL,'browser-owner-v1',NULL)
            """,
            (
                lease_id,
                _pub("rle", lease_id),
                fixture.tenant_id,
                fixture.resource_pub_id,
                expires_at,
                fixture.project_id,
                fixture.resource_id,
                capacity_id,
                operation_id,
                fixture.binding_id,
                f"lease-{lease_id}",
                acquired_at,
                heartbeat_at,
            ),
        )
    return (capacity_id, lease_id)


def _terminate_formal_lease(
    dsn: str,
    fixture: _ServiceFixture,
    *,
    capacity_id: UUID,
    lease_id: UUID,
) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        released = connection.execute(
            "UPDATE platform.resource_lease "
            "SET lease_state='released',released_at=now(),"
            "reconciliation_reason='owner_proved_not_sent',"
            "version=version+1,updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s "
            "AND lease_state='active' RETURNING id",
            (lease_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
        assert released == (lease_id,)
        connection.execute(
            "UPDATE platform.collection_resource_capacity_unit "
            "SET capacity_state='available',state_reason='integration_released',"
            "version=version+1,updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s",
            (capacity_id, fixture.tenant_id, fixture.project_id),
        )


def test_real_service_reserve_is_atomic_idempotent_and_reconciles_unknown() -> None:
    dsn = _test_dsn()
    fixture = _seed_service_fixture(dsn)
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result(timeout=20)
            for future in (
                pool.submit(
                    _reserve_with_service,
                    dsn,
                    fixture,
                    fixture.operation_ids[0],
                    barrier,
                ),
                pool.submit(
                    _reserve_with_service,
                    dsn,
                    fixture,
                    fixture.operation_ids[1],
                    barrier,
                ),
            )
        )

    assert sorted(result.reserved for result in results) == [False, True]
    winner_index = next(index for index, result in enumerate(results) if result.reserved)
    loser_index = 1 - winner_index
    winner_operation = fixture.operation_ids[winner_index]
    loser_operation = fixture.operation_ids[loser_index]
    winner = results[winner_index]
    assert winner.reservation_id is not None
    assert _service_bucket_keys(dsn, fixture) == fixture.bucket_keys
    assert _service_bucket_projection(dsn, fixture) == ((1, 0, 0), (1, 0, 0))

    replay = _reserve_with_service(dsn, fixture, winner_operation)
    assert replay.reserved is True
    assert replay.idempotent is True
    assert replay.reservation_id == winner.reservation_id
    assert _service_bucket_projection(dsn, fixture) == ((1, 0, 0), (1, 0, 0))

    released = _settle_with_service(dsn, fixture, winner_operation)
    released_replay = _settle_with_service(dsn, fixture, winner_operation)
    assert released.disposition is ReservationDisposition.RELEASED
    assert released.idempotent is False
    assert released_replay.disposition is ReservationDisposition.RELEASED
    assert released_replay.idempotent is True
    assert _service_bucket_projection(dsn, fixture) == ((0, 0, 0), (0, 0, 0))

    loser_reserved = _reserve_with_service(dsn, fixture, loser_operation)
    assert loser_reserved.reserved is True
    assert loser_reserved.idempotent is False
    assert _service_bucket_projection(dsn, fixture) == ((1, 0, 0), (1, 0, 0))

    _mark_operation_sending(dsn, fixture, loser_operation)

    request = ReconcileQuotaRequest(
        tenant_id=fixture.tenant_id,
        project_id=fixture.project_id,
        operation_id=loser_operation,
        owner_evidence=OwnerEvidence.ACKNOWLEDGEMENT_UNKNOWN,
        lease_terminated=False,
        reason_code="integration_ack_unknown",
    )
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        reconciled = reconcile_quota(cast(ConnectionProtocol, connection), request)
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        reconciled_replay = reconcile_quota(cast(ConnectionProtocol, connection), request)

    assert reconciled.action is ReconciliationAction.SETTLE_UNKNOWN
    assert reconciled.settlement is not None
    assert reconciled.settlement.disposition is ReservationDisposition.UNKNOWN_CONSUMED
    assert reconciled.settlement.idempotent is False
    assert reconciled_replay.action is ReconciliationAction.SETTLE_UNKNOWN
    assert reconciled_replay.settlement is not None
    assert reconciled_replay.settlement.idempotent is True
    assert _service_bucket_projection(dsn, fixture) == ((0, 0, 1), (0, 0, 1))

    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        counts = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM platform.collection_quota_reservation "
            " WHERE tenant_id=%s AND project_id=%s),"
            "(SELECT count(*) FROM platform.collection_quota_reservation_effect "
            " WHERE tenant_id=%s AND project_id=%s),"
            "(SELECT count(*) FROM platform.collection_quota_ledger_event "
            " WHERE tenant_id=%s AND project_id=%s)",
            (
                fixture.tenant_id,
                fixture.project_id,
                fixture.tenant_id,
                fixture.project_id,
                fixture.tenant_id,
                fixture.project_id,
            ),
        ).fetchone()
    assert counts == (2, 4, 8)


def test_real_sending_release_requires_and_persists_owner_proof() -> None:
    dsn = _test_dsn()
    fixture = _seed_service_fixture(dsn)
    operation_id = fixture.operation_ids[0]

    reserved = _reserve_with_service(dsn, fixture, operation_id)
    assert reserved.reserved is True
    _mark_operation_sending(dsn, fixture, operation_id)
    capacity_id, lease_id = _create_formal_lease(dsn, fixture, operation_id)

    with pytest.raises(
        QuotaV2Error,
        match="sending_quota_release_requires_reconciliation",
    ):
        _settle_with_service(dsn, fixture, operation_id)
    assert _service_bucket_projection(dsn, fixture) == ((1, 0, 0), (1, 0, 0))

    request = ReconcileQuotaRequest(
        tenant_id=fixture.tenant_id,
        project_id=fixture.project_id,
        operation_id=operation_id,
        owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
        lease_terminated=True,
        reason_code="integration_owner_proved_not_sent",
        owner_gateway_revision="browser-owner-v1",
        owner_evidence_ref=f"owner-proof-{operation_id}",
        evidence_hash=sha256(f"not-sent:{operation_id}".encode()).hexdigest(),
    )
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        with pytest.raises(
            psycopg.Error,
            match="not-sent proof requires every formal lease terminated",
        ):
            reconcile_quota(cast(ConnectionProtocol, connection), request)
    assert _service_bucket_projection(dsn, fixture) == ((1, 0, 0), (1, 0, 0))
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        proof_count = connection.execute(
            "SELECT count(*) FROM platform.collection_submission_reconciliation_proof "
            "WHERE tenant_id=%s AND project_id=%s AND operation_id=%s",
            (fixture.tenant_id, fixture.project_id, operation_id),
        ).fetchone()
    assert proof_count == (0,)

    _terminate_formal_lease(
        dsn,
        fixture,
        capacity_id=capacity_id,
        lease_id=lease_id,
    )
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        reconciled = reconcile_quota(cast(ConnectionProtocol, connection), request)
    with psycopg.connect(dsn) as connection:
        _activate_worker_role(connection)
        replay = reconcile_quota(cast(ConnectionProtocol, connection), request)

    assert reconciled.action is ReconciliationAction.RELEASE
    assert reconciled.settlement is not None
    assert reconciled.settlement.disposition is ReservationDisposition.RELEASED
    assert reconciled.settlement.idempotent is False
    assert replay.action is ReconciliationAction.RELEASE
    assert replay.settlement is not None
    assert replay.settlement.idempotent is True
    assert _service_bucket_projection(dsn, fixture) == ((0, 0, 0), (0, 0, 0))

    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        proof = connection.execute(
            "SELECT proof_state,owner_gateway_revision,owner_evidence_ref,evidence_hash,"
            "terminated_lease_count "
            "FROM platform.collection_submission_reconciliation_proof "
            "WHERE tenant_id=%s AND project_id=%s AND operation_id=%s",
            (fixture.tenant_id, fixture.project_id, operation_id),
        ).fetchone()
        terminal = connection.execute(
            "SELECT op.send_state,reservation.reservation_state "
            "FROM platform.collection_submission_operation AS op "
            "JOIN platform.collection_quota_reservation AS reservation "
            "ON reservation.operation_id=op.id AND reservation.tenant_id=op.tenant_id "
            "AND reservation.project_id=op.project_id "
            "WHERE op.id=%s AND op.tenant_id=%s AND op.project_id=%s",
            (operation_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
    assert proof == (
        "accepted",
        request.owner_gateway_revision,
        request.owner_evidence_ref,
        request.evidence_hash,
        1,
    )
    assert terminal == ("CONFIRMED_NOT_SENT", "released")
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (str(fixture.tenant_id),),
        )
        lease = connection.execute(
            "SELECT lease_state,released_at IS NOT NULL FROM platform.resource_lease "
            "WHERE id=%s AND tenant_id=%s AND project_id=%s",
            (lease_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
    assert lease == ("released", True)
