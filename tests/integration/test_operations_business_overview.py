from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi.testclient import TestClient
from geo_platform.main import app
from geo_platform.tenancy.ids import new_pub_id

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)
OVERVIEW_PATH = "/api/v2/operations/business-overview"


def _bootstrap(client: TestClient, marker: str) -> tuple[str, str, dict[str, str]]:
    subject = f"business-overview-{marker}"
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    tenant_pub_id = str(body["tenant_pub_id"])
    return (
        tenant_pub_id,
        str(body["user_pub_id"]),
        {
            "X-Tenant-Id": tenant_pub_id,
            "X-Actor-Id": subject,
            "X-Actor-Role": "admin",
            "Idempotency-Key": "overview-" + secrets.token_hex(16),
        },
    )


def _create_project(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    customer_name: str,
) -> str:
    headers["Idempotency-Key"] = "overview-project-" + secrets.token_hex(16)
    response = client.post(
        "/api/v2/projects",
        headers=headers,
        json={"name": name, "customer_name": customer_name},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _member_headers(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    role: str,
    marker: str,
) -> dict[str, str]:
    subject = f"overview-{role}-{marker}"
    response = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": subject, "display_name": role.title(), "role": role},
    )
    assert response.status_code == 201, response.text
    return {
        "X-Tenant-Id": admin_headers["X-Tenant-Id"],
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
    }


def _tenant_and_project_ids(tenant_pub_id: str, project_pub_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    with psycopg.connect(POSTGRES_DSN) as connection:
        row = connection.execute(
            """
            SELECT tenant.id,project.id
            FROM platform.tenant tenant
            JOIN platform.project project ON project.tenant_id=tenant.id
            WHERE tenant.pub_id=%s AND project.pub_id=%s
            """,
            (tenant_pub_id, project_pub_id),
        ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0])), uuid.UUID(str(row[1]))


def _set_project_state(project_pub_id: str, state: str) -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            "UPDATE platform.project SET state=%s,updated_at=now() WHERE pub_id=%s",
            (state, project_pub_id),
        )


def _seed_setup(
    tenant_pub_id: str,
    project_pub_id: str,
    *,
    declared_by: str,
    truth_confirmed: bool | None,
) -> uuid.UUID:
    tenant_id, project_id = _tenant_and_project_ids(tenant_pub_id, project_pub_id)
    config_id = uuid.uuid4()
    config_version_id = uuid.uuid4()
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO platform.client_profile_version
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,revision,
               company_name,contact_role,audience,public_statement,declared_by)
            VALUES (%s,%s,%s,1,now(),now(),%s,1,'客户主体','项目负责人','目标受众',
                    '经客户确认的公开说明',%s)
            """,
            (uuid.uuid4(), new_pub_id("cpv"), tenant_id, project_id, declared_by),
        )
        connection.execute(
            """
            INSERT INTO platform.asset_confirmation_version
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,revision,
               brand_name,website,product_name,competitor_name,prohibited_claim,declared_by)
            VALUES (%s,%s,%s,1,now(),now(),%s,1,'确认品牌','https://example.test',
                    '确认产品','确认竞品','禁止夸大',%s)
            """,
            (uuid.uuid4(), new_pub_id("acv"), tenant_id, project_id, declared_by),
        )
        connection.execute(
            """
            INSERT INTO platform.monitoring_config
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,state,current_version)
            VALUES (%s,%s,%s,1,now(),now(),%s,'frozen',1)
            """,
            (config_id, new_pub_id("mcg"), tenant_id, project_id),
        )
        connection.execute(
            """
            INSERT INTO platform.monitoring_config_version
              (id,pub_id,tenant_id,version,created_at,updated_at,config_id,revision,
               effective_at,frozen_at,snapshot_json,snapshot_hash)
            VALUES (%s,%s,%s,1,now(),now(),%s,1,now(),now(),'{}',%s)
            """,
            (config_version_id, new_pub_id("mcv"), tenant_id, config_id, "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO platform.intake_profile
              (id,pub_id,tenant_id,project_id,version,created_at,updated_at,
               truth_confirmed,goals,audience_type,platforms,regions,trademarks,
               ad_review_doc_types,evidence_links,licenses,prefilled)
            VALUES (%s,%s,%s,%s,1,now(),now(),%s,'[]','[]','[]','[]','[]','[]','[]','[]','{}')
            """,
            (uuid.uuid4(), new_pub_id("inp"), tenant_id, project_id, truth_confirmed),
        )
    return config_version_id


def _seed_newer_setup_revisions(
    tenant_pub_id: str,
    project_pub_id: str,
    *,
    declared_by: str,
) -> None:
    tenant_id, project_id = _tenant_and_project_ids(tenant_pub_id, project_pub_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant_id),))
        config = connection.execute(
            "SELECT id FROM platform.monitoring_config WHERE tenant_id=%s AND project_id=%s",
            (tenant_id, project_id),
        ).fetchone()
        assert config is not None
        connection.execute(
            """
            INSERT INTO platform.client_profile_version
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,revision,
               company_name,contact_role,audience,public_statement,declared_by)
            VALUES (%s,%s,%s,1,now(),now(),%s,2,'客户主体二版','项目负责人','目标受众',
                    '经客户确认的二版公开说明',%s)
            """,
            (uuid.uuid4(), new_pub_id("cpv"), tenant_id, project_id, declared_by),
        )
        connection.execute(
            """
            INSERT INTO platform.asset_confirmation_version
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,revision,
               brand_name,website,product_name,competitor_name,prohibited_claim,declared_by)
            VALUES (%s,%s,%s,1,now(),now(),%s,2,'确认品牌二版','https://example.test/v2',
                    '确认产品','确认竞品','禁止夸大',%s)
            """,
            (uuid.uuid4(), new_pub_id("acv"), tenant_id, project_id, declared_by),
        )
        connection.execute(
            """
            INSERT INTO platform.monitoring_config_version
              (id,pub_id,tenant_id,version,created_at,updated_at,config_id,revision,
               effective_at,frozen_at,snapshot_json,snapshot_hash)
            VALUES (%s,%s,%s,1,now(),now(),%s,2,now(),now(),'{}',%s)
            """,
            (uuid.uuid4(), new_pub_id("mcv"), tenant_id, config[0], "d" * 64),
        )
        connection.execute(
            "UPDATE platform.monitoring_config SET current_version=2,updated_at=now() WHERE id=%s",
            (config[0],),
        )


def _seed_entitlement(
    tenant_pub_id: str,
    project_pub_id: str,
    *,
    service_code: str = "ranking_test",
    state: str = "active",
    authorized_from: datetime | None = None,
    authorized_until: datetime | None = None,
    catalog_version: str | None = None,
) -> None:
    tenant_id, project_id = _tenant_and_project_ids(tenant_pub_id, project_pub_id)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO platform.project_service_entitlement
              (id,pub_id,tenant_id,project_id,service_code,catalog_version,state,
               authorized_from,authorized_until,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
            """,
            (
                uuid.uuid4(),
                new_pub_id("ent"),
                tenant_id,
                project_id,
                service_code,
                catalog_version or f"overview-{secrets.token_hex(5)}",
                state,
                authorized_from,
                authorized_until,
            ),
        )


def _seed_delayed_run(
    tenant_pub_id: str,
    project_pub_id: str,
    config_version_id: uuid.UUID,
) -> None:
    tenant_id, project_id = _tenant_and_project_ids(tenant_pub_id, project_pub_id)
    stale_at = datetime.now(UTC) - timedelta(hours=1)
    marker = secrets.token_hex(8)
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (str(tenant_id),))
        connection.execute(
            """
            INSERT INTO platform.collection_run
              (id,pub_id,tenant_id,version,created_at,updated_at,project_id,
               config_version_id,idempotency_key,workflow_id,state,total_tasks,
               completed_tasks,failed_tasks,paused,source)
            VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'running',1,0,0,false,'manual')
            """,
            (
                uuid.uuid4(),
                new_pub_id("run"),
                tenant_id,
                stale_at,
                stale_at,
                project_id,
                config_version_id,
                f"overview-run-{marker}",
                f"overview/run/{marker}",
            ),
        )


def _create_formal_production(
    client: TestClient,
    headers: dict[str, str],
    project_pub_id: str,
) -> str:
    headers["Idempotency-Key"] = "overview-formal-" + secrets.token_hex(16)
    response = client.post(
        "/api/v2/reports/formal-productions",
        headers=headers,
        json={
            "project_pub_id": project_pub_id,
            "services": [1],
            "window_start": "2026-08-01",
            "window_end": "2026-08-20",
            "document_status": "internal_review",
            "candidate_group_strategy": "preregistered_scope_v1",
            "prepared_by": "Business overview integration",
            "prepared_date": "2026-08-21",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pub_id"])


def _set_production_state(tenant_pub_id: str, production_pub_id: str, state: str) -> None:
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant_pub_id,))
        connection.execute(
            """
            UPDATE reporting.formal_report_production
            SET status=%s,updated_at=now()
            WHERE tenant_pub_id=%s AND pub_id=%s
            """,
            (state, tenant_pub_id, production_pub_id),
        )


def _seed_pending_delivery(
    tenant_pub_id: str,
    project_pub_id: str,
    production_pub_id: str,
    actor_pub_id: str,
) -> None:
    report_pub_id = new_pub_id("rpt")
    version_pub_id = new_pub_id("rpv")
    fact_hash = "b" * 64
    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute("SELECT set_config('app.tenant_pub_id',%s,true)", (tenant_pub_id,))
        connection.execute(
            """
            INSERT INTO reporting.report
              (pub_id,tenant_pub_id,project_pub_id,title,state,created_at,updated_at)
            VALUES (%s,%s,%s,'商务总览交付测试','published',now(),now())
            """,
            (report_pub_id, tenant_pub_id, project_pub_id),
        )
        connection.execute(
            """
            INSERT INTO reporting.report_version
              (pub_id,tenant_pub_id,report_pub_id,version_number,window_start,window_end,
               filters,filter_hash,metric_version,scorer_version,fact_snapshot_hash,
               status,created_by_pub_id,created_at)
            VALUES (%s,%s,%s,1,now()-interval '1 day',now(),'{}',%s,'overview-v1',
                    'overview-v1',%s,'published',%s,now())
            """,
            (version_pub_id, tenant_pub_id, report_pub_id, "c" * 64, fact_hash, actor_pub_id),
        )
        connection.execute(
            """
            INSERT INTO reporting.formal_report_output
              (pub_id,tenant_pub_id,production_pub_id,service_number,report_pub_id,
               report_version_pub_id,fact_snapshot_hash,created_at)
            VALUES (%s,%s,%s,1,%s,%s,%s,now())
            """,
            (
                new_pub_id("fro"),
                tenant_pub_id,
                production_pub_id,
                report_pub_id,
                version_pub_id,
                fact_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO reporting.report_delivery
              (pub_id,tenant_pub_id,report_pub_id,recipient_pub_id,delivered_at,confirmed_at)
            VALUES (%s,%s,%s,%s,now(),NULL)
            """,
            (new_pub_id("dlv"), tenant_pub_id, report_pub_id, actor_pub_id),
        )


def test_business_overview_paginates_filters_and_isolates_tenants() -> None:
    client = TestClient(app)
    marker = secrets.token_hex(7)
    tenant_pub_id, _actor, headers = _bootstrap(client, marker)
    states = ("draft", "active", "paused", "archived", "active")
    expected_projects: set[str] = set()
    for index, state in enumerate(states, start=1):
        project_pub_id = _create_project(
            client,
            headers,
            name=f"商务组合 {index}",
            customer_name=f"客户 {index}",
        )
        _set_project_state(project_pub_id, state)
        expected_projects.add(project_pub_id)

    foreign_tenant, _foreign_actor, foreign_headers = _bootstrap(client, "foreign-" + marker)
    foreign_project = _create_project(
        client,
        foreign_headers,
        name="FOREIGN-MUST-NOT-LEAK",
        customer_name="FOREIGN-CUSTOMER",
    )

    first = client.get(OVERVIEW_PATH, headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["vary"] == "Cookie, Authorization"
    assert first_body["schema_version"] == 1
    assert first_body["summary"] == {
        "scope": "filtered",
        "tenant_project_count": 5,
        "project_count": 5,
        "project_state_counts": {"draft": 1, "active": 2, "paused": 1, "archived": 1},
        "setup_ready_project_count": 0,
        "project_with_entitlement_record_count": 0,
        "active_entitlement_count": 0,
        "attention_project_count": 5,
    }
    assert first_body["commercial_capabilities"] == {
        "quotation_history": "unsupported",
        "signed_contract_ledger": "unsupported",
        "invoice_receivable_payment_ledger": "unsupported",
    }
    assert len(first_body["items"]) == 4
    assert first_body["page"]["limit"] == 4
    assert first_body["page"]["has_more"] is True
    assert first_body["page"]["filtered_total"] == 5
    cursor = first_body["page"]["next_cursor"]
    assert isinstance(cursor, str) and cursor

    second = client.get(OVERVIEW_PATH, headers=headers, params={"cursor": cursor})
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["page"]["has_more"] is False
    assert second_body["page"]["next_cursor"] is None
    first_ids = {item["project"]["id"] for item in first_body["items"]}
    second_ids = {item["project"]["id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == expected_projects
    assert foreign_project not in first.text + second.text
    assert "FOREIGN-MUST-NOT-LEAK" not in first.text + second.text

    active = client.get(
        OVERVIEW_PATH, headers=headers, params={"project_state": "active", "limit": 20}
    )
    assert active.status_code == 200, active.text
    assert active.json()["summary"]["project_count"] == 2
    assert {item["project"]["state"] for item in active.json()["items"]} == {"active"}

    empty = client.get(OVERVIEW_PATH, headers=headers, params={"q": "不存在的客户"})
    assert empty.status_code == 200, empty.text
    assert empty.json()["summary"]["tenant_project_count"] == 5
    assert empty.json()["summary"]["project_count"] == 0
    assert empty.json()["items"] == []
    assert empty.json()["page"]["filtered_total"] == 0

    escaped_wildcard = client.get(OVERVIEW_PATH, headers=headers, params={"q": "%_"})
    assert escaped_wildcard.status_code == 200, escaped_wildcard.text
    assert escaped_wildcard.json()["items"] == []

    foreign = client.get(OVERVIEW_PATH, headers=foreign_headers)
    assert foreign.status_code == 200, foreign.text
    assert foreign.json()["summary"]["tenant_project_count"] == 1
    assert [item["project"]["id"] for item in foreign.json()["items"]] == [foreign_project]

    malformed = client.get(OVERVIEW_PATH, headers=headers, params={"cursor": "bad"})
    assert malformed.status_code == 400
    mismatch = client.get(
        OVERVIEW_PATH,
        headers=headers,
        params={"cursor": cursor, "project_state": "active"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "cursor_filter_mismatch"
    assert client.get(OVERVIEW_PATH, headers=headers, params={"limit": 0}).status_code == 422
    assert client.get(OVERVIEW_PATH, headers=headers, params={"limit": 21}).status_code == 422
    assert (
        client.get(OVERVIEW_PATH, headers=headers, params={"project_state": "deleted"}).status_code
        == 422
    )
    assert client.get(OVERVIEW_PATH, headers=headers, params={"q": "x" * 121}).status_code == 422
    assert tenant_pub_id not in first.text


def test_business_overview_uses_a_dedicated_internal_role_permission() -> None:
    client = TestClient(app)
    marker = secrets.token_hex(7)
    _tenant, _actor, admin_headers = _bootstrap(client, marker)
    _create_project(client, admin_headers, name="权限项目", customer_name="权限客户")

    role_headers = {
        role: _member_headers(
            client,
            admin_headers,
            role=role,
            marker=marker,
        )
        for role in ("operator", "analyst", "reviewer", "customer", "worker")
    }
    for role in ("operator", "analyst", "reviewer"):
        response = client.get(OVERVIEW_PATH, headers=role_headers[role])
        assert response.status_code == 200, (role, response.text)
    assert client.get(OVERVIEW_PATH, headers=admin_headers).status_code == 200
    for role in ("customer", "worker"):
        response = client.get(OVERVIEW_PATH, headers=role_headers[role])
        assert response.status_code == 403, (role, response.text)
        assert response.json()["error"]["code"] == "permission_denied"


def test_business_overview_preserves_unknowns_windows_and_attention_priority() -> None:
    client = TestClient(app)
    marker = secrets.token_hex(7)
    tenant_pub_id, actor_pub_id, headers = _bootstrap(client, marker)

    unknown = _create_project(client, headers, name="未知事实项目", customer_name="未知事实客户")
    ready = _create_project(client, headers, name="配置就绪项目", customer_name="配置就绪客户")
    intake_false = _create_project(
        client, headers, name="待确认事实项目", customer_name="待确认事实客户"
    )
    windowed = _create_project(client, headers, name="权益窗口项目", customer_name="权益窗口客户")
    collection = _create_project(client, headers, name="采集优先项目", customer_name="采集优先客户")
    failed = _create_project(client, headers, name="生产失败项目", customer_name="生产失败客户")
    review = _create_project(client, headers, name="待审核项目", customer_name="待审核客户")
    delivery_only = _create_project(
        client, headers, name="待确认交付项目", customer_name="待确认交付客户"
    )

    for project in (ready, collection, failed, review, delivery_only):
        config_version = _seed_setup(
            tenant_pub_id,
            project,
            declared_by=actor_pub_id,
            truth_confirmed=True,
        )
        _seed_entitlement(tenant_pub_id, project)
        if project == collection:
            _seed_delayed_run(tenant_pub_id, project, config_version)
    _seed_newer_setup_revisions(
        tenant_pub_id,
        ready,
        declared_by=actor_pub_id,
    )

    _seed_setup(
        tenant_pub_id,
        intake_false,
        declared_by=actor_pub_id,
        truth_confirmed=False,
    )
    _seed_entitlement(tenant_pub_id, intake_false)
    _seed_setup(
        tenant_pub_id,
        windowed,
        declared_by=actor_pub_id,
        truth_confirmed=True,
    )
    now = datetime.now(UTC)
    _seed_entitlement(
        tenant_pub_id,
        windowed,
        state="active",
        authorized_from=now + timedelta(days=2),
        authorized_until=now + timedelta(days=10),
    )
    _seed_entitlement(
        tenant_pub_id,
        windowed,
        service_code="outbound_disparagement_audit",
        state="active",
        catalog_version="overview-outbound-old",
    )
    _seed_entitlement(
        tenant_pub_id,
        windowed,
        service_code="outbound_disparagement_audit",
        state="suspended",
    )
    _seed_entitlement(
        tenant_pub_id,
        windowed,
        service_code="inbound_disparagement_audit",
        state="expired",
        authorized_from=now - timedelta(days=10),
        authorized_until=now - timedelta(days=1),
    )
    _seed_entitlement(
        tenant_pub_id,
        windowed,
        service_code="official_site_audit",
        state="inactive",
    )

    delivery_production = _create_formal_production(client, headers, collection)
    _seed_pending_delivery(
        tenant_pub_id,
        collection,
        delivery_production,
        actor_pub_id,
    )
    _set_production_state(tenant_pub_id, delivery_production, "signed")
    failed_production = _create_formal_production(client, headers, failed)
    _set_production_state(tenant_pub_id, failed_production, "failed")
    review_production = _create_formal_production(client, headers, review)
    _set_production_state(tenant_pub_id, review_production, "awaiting_review")
    pending_production = _create_formal_production(client, headers, delivery_only)
    _seed_pending_delivery(
        tenant_pub_id,
        delivery_only,
        pending_production,
        actor_pub_id,
    )
    _set_production_state(tenant_pub_id, pending_production, "signed")

    tenant_id, _project_id = _tenant_and_project_ids(tenant_pub_id, ready)
    with psycopg.connect(POSTGRES_DSN) as connection:
        before_updates = connection.execute(
            "SELECT pub_id,updated_at FROM platform.project WHERE tenant_id=%s ORDER BY pub_id",
            (tenant_id,),
        ).fetchall()
        before_audits = connection.execute(
            "SELECT count(*) FROM platform.audit_log WHERE tenant_id=%s", (tenant_id,)
        ).fetchone()

    response = client.get(OVERVIEW_PATH, headers=headers, params={"limit": 20})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["tenant_project_count"] == 8
    assert body["summary"]["setup_ready_project_count"] == 7
    assert body["summary"]["project_with_entitlement_record_count"] == 7
    assert body["summary"]["active_entitlement_count"] == 6
    by_project = {item["project"]["id"]: item for item in body["items"]}

    assert by_project[unknown]["setup"] == {
        "client_profile_revision": None,
        "asset_confirmation_revision": None,
        "frozen_monitoring_config_revision": None,
        "setup_ready": False,
        "intake_profile_exists": False,
        "intake_truth_confirmed": None,
    }
    assert by_project[unknown]["service_entitlements"] == []
    assert by_project[unknown]["primary_attention"] == {
        "code": "setup_records_missing",
        "severity": "warning",
        "additional_count": 2,
    }

    assert by_project[ready]["setup"]["setup_ready"] is True
    assert by_project[ready]["setup"]["client_profile_revision"] == 2
    assert by_project[ready]["setup"]["asset_confirmation_revision"] == 2
    assert by_project[ready]["setup"]["frozen_monitoring_config_revision"] == 2
    assert by_project[ready]["setup"]["intake_truth_confirmed"] is True
    assert by_project[ready]["primary_attention"]["code"] == "no_current_attention"
    assert by_project[intake_false]["setup"]["intake_truth_confirmed"] is False
    assert (
        by_project[intake_false]["primary_attention"]["code"]
        == "intake_truth_confirmation_required"
    )

    entitlements = {
        item["service_code"]: item for item in by_project[windowed]["service_entitlements"]
    }
    assert entitlements["ranking_test"]["state"] == "active"
    assert entitlements["ranking_test"]["effective_now"] is False
    assert entitlements["ranking_test"]["authorized_from"] is not None
    assert entitlements["outbound_disparagement_audit"]["state"] == "suspended"
    assert entitlements["outbound_disparagement_audit"]["effective_now"] is False
    assert entitlements["inbound_disparagement_audit"]["state"] == "expired"
    assert entitlements["inbound_disparagement_audit"]["effective_now"] is False
    assert entitlements["official_site_audit"]["state"] == "inactive"
    assert entitlements["official_site_audit"]["effective_now"] is False
    assert by_project[windowed]["primary_attention"]["code"] == "no_current_attention"

    assert by_project[collection]["collection"]["active_count"] == 1
    assert by_project[collection]["collection"]["delayed_count"] == 1
    assert by_project[collection]["collection"]["latest_state"] == "running"
    assert by_project[collection]["formal_report"]["latest_state"] == "signed"
    assert by_project[collection]["delivery"]["pending_confirmation_count"] == 1
    assert by_project[collection]["delivery"]["confirmed_at"] is None
    assert by_project[collection]["primary_attention"] == {
        "code": "collection_failed_or_delayed",
        "severity": "danger",
        "additional_count": 1,
    }
    assert by_project[collection]["contract_draft_export"] is None

    assert by_project[failed]["formal_report"]["latest_state"] == "failed"
    assert by_project[failed]["primary_attention"]["code"] == "formal_production_failed"
    assert by_project[failed]["primary_attention"]["severity"] == "danger"
    assert by_project[review]["formal_report"]["latest_state"] == "awaiting_review"
    assert by_project[review]["primary_attention"]["code"] == "formal_review_required"
    assert by_project[review]["primary_attention"]["severity"] == "warning"
    assert by_project[delivery_only]["formal_report"]["latest_state"] == "signed"
    assert by_project[delivery_only]["delivery"]["pending_confirmation_count"] == 1
    assert (
        by_project[delivery_only]["primary_attention"]["code"] == "delivery_confirmation_required"
    )
    assert by_project[delivery_only]["primary_attention"]["severity"] == "warning"

    filtered = client.get(
        OVERVIEW_PATH,
        headers=headers,
        params={"attention": "formal_review_required", "limit": 20},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["summary"]["project_count"] == 1
    assert [item["project"]["id"] for item in filtered.json()["items"]] == [review]
    serialized = json.dumps(body, ensure_ascii=False).lower()
    assert all(
        field not in serialized
        for field in ("quotation_pub_id", "contract_pub_id", "invoice_pub_id", "payment_pub_id")
    )
    assert actor_pub_id not in response.text
    assert tenant_pub_id not in response.text
    with psycopg.connect(POSTGRES_DSN) as connection:
        after_updates = connection.execute(
            "SELECT pub_id,updated_at FROM platform.project WHERE tenant_id=%s ORDER BY pub_id",
            (tenant_id,),
        ).fetchall()
        after_audits = connection.execute(
            "SELECT count(*) FROM platform.audit_log WHERE tenant_id=%s", (tenant_id,)
        ).fetchone()
    assert after_updates == before_updates
    assert after_audits == before_audits
