from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.intelligence.router import router
from geo_platform.tenancy.psycopg import tenant_connection
from psycopg.rows import dict_row

POSTGRES_DSN = os.getenv(
    "S02_POSTGRES_DSN", "postgresql://geo:geo_dev_only@127.0.0.1:55433/geo_platform"
)

REQUIRED_EXPLANATION_FIELDS = [
    "evidence_sufficiency",
    "human_verdict_state",
    "independent_source_count",
    "model_version",
    "rule_version",
    "uncertainty",
]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _principal(role: Role, tenant_pub_id: str, user_pub_id: str) -> Principal:
    return Principal(
        subject=f"subject-{user_pub_id}",
        role=role,
        tenant_pub_id=tenant_pub_id,
        user_pub_id=user_pub_id,
    )


def _seed_source_evidence(tenant_pub_id: str, evidence_pub_id: str, digest: str) -> None:
    with tenant_connection(POSTGRES_DSN, tenant_pub_id) as connection:
        connection.execute(
            """
            INSERT INTO evidence.evidence_asset (
              pub_id,tenant_pub_id,kind,access_class,sha256,object_key,mime_type,
              byte_size,channel,adapter_version,capture_time
            ) VALUES (
              %s,%s,'anti_geo_calibration_dataset','customer_private',%s,%s,
              'application/json',4096,'api','external-authorized-import-v1',%s
            )
            """,
            (
                evidence_pub_id,
                tenant_pub_id,
                digest,
                f"sha256/{digest}",
                datetime.now(UTC),
            ),
        )


def _dataset_body(
    *, version: str, source_artifact_pub_id: str, source_artifact_sha256: str
) -> dict[str, Any]:
    return {
        "version": version,
        "source_artifact_pub_id": source_artifact_pub_id,
        "source_artifact_sha256": source_artifact_sha256,
        "label_policy_version": "anti-geo-human-label-v1",
        "labeler_count": 2,
        "cases": [
            {
                "case_digest": _digest(f"case-{index}"),
                "propagation_cluster_digest": _digest(f"cluster-{index}"),
                "actual_positive": index < 10,
            }
            for index in range(20)
        ],
    }


def _predictions(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "case_digest": item["case_digest"],
            "probability": 0.9 if item["actual_positive"] else 0.1,
            "predicted_positive": item["actual_positive"],
            "explanation_fields": REQUIRED_EXPLANATION_FIELDS,
        }
        for item in body["cases"]
    ]


def test_governed_anti_geo_dataset_evaluation_and_model_admission() -> None:
    suffix = uuid4().hex
    tenant_pub_id = f"tnt_eval_{suffix[:16]}"
    foreign_tenant_pub_id = f"tnt_eval_foreign_{suffix[:12]}"
    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    source_pub_id = f"evd_eval_source_{suffix}"
    source_sha256 = _digest(f"authorized-source-{suffix}")
    analyst_pub_id = f"usr_eval_analyst_{suffix[:12]}"
    reviewer_pub_id = f"usr_eval_reviewer_{suffix[:12]}"
    principal: dict[str, Principal] = {
        "current": _principal(Role.ANALYST, tenant_pub_id, analyst_pub_id)
    }
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: principal["current"]
    client = TestClient(app)

    with psycopg.connect(POSTGRES_DSN) as connection:
        connection.execute(
            """
            INSERT INTO platform.tenant (id,pub_id,name,state,created_at,updated_at)
            VALUES (%s,%s,'Anti-GEO evaluation tenant','active',now(),now()),
                   (%s,%s,'Anti-GEO foreign tenant','active',now(),now())
            """,
            (tenant_id, tenant_pub_id, foreign_tenant_id, foreign_tenant_pub_id),
        )
    _seed_source_evidence(tenant_pub_id, source_pub_id, source_sha256)

    dataset_key = f"dataset-registration-{suffix}"
    evaluation_key = f"dataset-evaluation-{suffix}"
    admission_key = f"model-admission-{suffix}"
    dataset_body = _dataset_body(
        version=f"external-approved-{suffix}",
        source_artifact_pub_id=source_pub_id,
        source_artifact_sha256=source_sha256,
    )
    try:
        missing_source_body = dict(dataset_body)
        missing_source_body["version"] = f"missing-source-{suffix}"
        missing_source_body["source_artifact_pub_id"] = f"evd_missing_{suffix}"
        response = client.post(
            "/api/v2/intelligence/evaluation-datasets",
            headers={"Idempotency-Key": f"missing-source-{suffix}"},
            json=missing_source_body,
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "dataset_source_artifact_not_found"

        response = client.post(
            "/api/v2/intelligence/evaluation-datasets",
            headers={"Idempotency-Key": dataset_key},
            json=dataset_body,
        )
        assert response.status_code == 201, response.text
        dataset = response.json()
        dataset_pub_id = dataset["pub_id"]
        assert dataset["state"] == "draft"
        assert dataset["case_count"] == 20
        assert dataset["positive_count"] == 10
        assert set(dataset) == {
            "pub_id",
            "version",
            "dataset_sha256",
            "state",
            "case_count",
            "positive_count",
            "labeler_count",
            "submitted_at",
            "approved_at",
        }

        replay = client.post(
            "/api/v2/intelligence/evaluation-datasets",
            headers={"Idempotency-Key": dataset_key},
            json=dataset_body,
        )
        assert replay.status_code == 201
        assert replay.json() == dataset

        drifted_body = dict(dataset_body)
        drifted_body["labeler_count"] = 3
        drifted = client.post(
            "/api/v2/intelligence/evaluation-datasets",
            headers={"Idempotency-Key": dataset_key},
            json=drifted_body,
        )
        assert drifted.status_code == 409
        assert drifted.json()["detail"]["code"] == "dataset_idempotency_conflict"

        principal["current"] = _principal(Role.ADMIN, tenant_pub_id, analyst_pub_id)
        self_approval = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve",
            json={"rationale": "independent calibration review complete"},
        )
        assert self_approval.status_code == 403
        assert self_approval.json()["detail"]["code"] == "dataset_independent_reviewer_required"

        principal["current"] = _principal(Role.REVIEWER, foreign_tenant_pub_id, reviewer_pub_id)
        foreign_list = client.get("/api/v2/intelligence/evaluation-datasets")
        assert foreign_list.status_code == 200
        assert foreign_list.json()["data"] == []
        foreign_approval = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve",
            json={"rationale": "foreign tenant must not observe this dataset"},
        )
        assert foreign_approval.status_code == 404

        principal["current"] = _principal(Role.REVIEWER, tenant_pub_id, reviewer_pub_id)
        approval_body = {"rationale": "external labels and source evidence independently reviewed"}
        approved = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve",
            json=approval_body,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "approved"
        approval_replay = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/approve",
            json=approval_body,
        )
        assert approval_replay.status_code == 200
        assert approval_replay.json() == approved.json()

        with pytest.raises(psycopg.errors.RaiseException):
            with tenant_connection(POSTGRES_DSN, tenant_pub_id) as connection:
                connection.execute(
                    """
                    UPDATE evidence.evidence_asset
                    SET deleted_at=now()
                    WHERE tenant_pub_id=%s AND pub_id=%s
                    """,
                    (tenant_pub_id, source_pub_id),
                )

        principal["current"] = _principal(Role.ANALYST, tenant_pub_id, analyst_pub_id)
        predictions = _predictions(dataset_body)
        invalid_predictions = [dict(item) for item in predictions]
        invalid_predictions[0]["explanation_fields"] = ["untrusted_free_text"]
        invalid_explanation = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs",
            headers={"Idempotency-Key": f"invalid-explanation-{suffix}"},
            json={
                "scorer_version": f"anti-geo-scorer-{suffix}",
                "predictions": invalid_predictions,
            },
        )
        assert invalid_explanation.status_code == 422
        assert invalid_explanation.json()["detail"]["code"] == "evaluation_contract_invalid"

        run_body = {
            "scorer_version": f"anti-geo-scorer-{suffix}",
            "decision_threshold": 0.5,
            "calibration_bins": 10,
            "predictions": predictions,
        }
        overlap_run_body = {
            **run_body,
            "training_propagation_cluster_digests": [
                dataset_body["cases"][0]["propagation_cluster_digest"]
            ],
        }
        overlap = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs",
            headers={"Idempotency-Key": f"training-overlap-{suffix}"},
            json=overlap_run_body,
        )
        assert overlap.status_code == 422
        assert overlap.json()["detail"]["code"] == "evaluation_contract_invalid"

        evaluated = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs",
            headers={"Idempotency-Key": evaluation_key},
            json=run_body,
        )
        assert evaluated.status_code == 201, evaluated.text
        evaluation = evaluated.json()
        evaluation_run_pub_id = evaluation["pub_id"]
        assert evaluation["admission_passed"] is True
        assert all(evaluation["admission_checks"].values())
        assert evaluation["metrics"]["sample_count"] == 20
        assert evaluation["training_cluster_count"] == 0
        assert len(evaluation["training_cluster_manifest_sha256"]) == 64
        assert evaluation["required_explanation_fields"] == REQUIRED_EXPLANATION_FIELDS
        assert "contract_hash" not in evaluation
        listed_runs = client.get("/api/v2/intelligence/evaluation-runs")
        assert listed_runs.status_code == 200
        assert listed_runs.json()["data"][0]["pub_id"] == evaluation_run_pub_id
        assert listed_runs.json()["data"][0]["model_admission_state"] is None

        evaluation_replay = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs",
            headers={"Idempotency-Key": evaluation_key},
            json=run_body,
        )
        assert evaluation_replay.status_code == 201
        assert evaluation_replay.json() == evaluation

        drifted_run = dict(run_body)
        drifted_run["scorer_version"] = f"drifted-scorer-{suffix}"
        evaluation_drift = client.post(
            f"/api/v2/intelligence/evaluation-datasets/{dataset_pub_id}/runs",
            headers={"Idempotency-Key": evaluation_key},
            json=drifted_run,
        )
        assert evaluation_drift.status_code == 409
        assert evaluation_drift.json()["detail"]["code"] == "evaluation_idempotency_conflict"

        principal["current"] = _principal(Role.ADMIN, tenant_pub_id, analyst_pub_id)
        self_admission = client.post(
            f"/api/v2/intelligence/evaluation-runs/{evaluation_run_pub_id}/admit",
            headers={"Idempotency-Key": admission_key},
            json={"rationale": "threshold and calibration checks independently accepted"},
        )
        assert self_admission.status_code == 403
        assert self_admission.json()["detail"]["code"] == "model_independent_reviewer_required"

        principal["current"] = _principal(Role.REVIEWER, tenant_pub_id, reviewer_pub_id)
        admission_body = {"rationale": "threshold and calibration checks independently accepted"}
        admitted = client.post(
            f"/api/v2/intelligence/evaluation-runs/{evaluation_run_pub_id}/admit",
            headers={"Idempotency-Key": admission_key},
            json=admission_body,
        )
        assert admitted.status_code == 201, admitted.text
        model_admission = admitted.json()
        assert model_admission["state"] == "admitted"
        assert "contract_hash" not in model_admission
        admission_replay = client.post(
            f"/api/v2/intelligence/evaluation-runs/{evaluation_run_pub_id}/admit",
            headers={"Idempotency-Key": admission_key},
            json=admission_body,
        )
        assert admission_replay.status_code == 201
        assert admission_replay.json() == model_admission

        listed = client.get("/api/v2/intelligence/model-admissions")
        assert listed.status_code == 200
        assert [item["pub_id"] for item in listed.json()["data"]] == [model_admission["pub_id"]]
        listed_runs = client.get("/api/v2/intelligence/evaluation-runs")
        assert listed_runs.status_code == 200
        assert listed_runs.json()["data"][0]["model_admission_state"] == "admitted"

        with tenant_connection(POSTGRES_DSN, tenant_pub_id, row_factory=dict_row) as connection:
            stored_dataset = connection.execute(
                """
                SELECT registration_operation_hash,registration_contract_hash
                FROM intelligence.evaluation_dataset
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, dataset_pub_id),
            ).fetchone()
            stored_run = connection.execute(
                """
                SELECT operation_hash,contract_hash
                FROM intelligence.evaluation_run
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, evaluation_run_pub_id),
            ).fetchone()
            stored_admission = connection.execute(
                """
                SELECT operation_hash,contract_hash
                FROM intelligence.model_admission
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, model_admission["pub_id"]),
            ).fetchone()
        assert stored_dataset is not None
        assert stored_run is not None
        assert stored_admission is not None
        for row, raw_key in (
            (stored_dataset, dataset_key),
            (stored_run, evaluation_key),
            (stored_admission, admission_key),
        ):
            assert raw_key not in row.values()
            assert all(len(str(value)) == 64 for value in row.values())
        with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (str(tenant_id),),
            )
            audit_rows = connection.execute(
                """
                SELECT action,count(*) AS event_count,string_agg(receipt,' ') AS receipts
                FROM platform.audit_log
                WHERE tenant_id=%s
                  AND action LIKE 'intelligence.%%'
                GROUP BY action
                ORDER BY action
                """,
                (tenant_id,),
            ).fetchall()
        assert {row["action"]: row["event_count"] for row in audit_rows} == {
            "intelligence.evaluation_dataset.approved": 1,
            "intelligence.evaluation_dataset.registered": 1,
            "intelligence.evaluation_run.created": 1,
            "intelligence.model_admission.created": 1,
        }
        assert all(
            raw_key not in str(row["receipts"])
            for row in audit_rows
            for raw_key in (dataset_key, evaluation_key, admission_key)
        )
    finally:
        with tenant_connection(POSTGRES_DSN, tenant_pub_id) as connection:
            connection.execute(
                "DELETE FROM intelligence.model_admission WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM intelligence.evaluation_case_result WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM intelligence.evaluation_run WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM intelligence.evaluation_dataset_case WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM intelligence.evaluation_dataset WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
            connection.execute(
                "DELETE FROM evidence.evidence_asset WHERE tenant_pub_id=%s",
                (tenant_pub_id,),
            )
        with psycopg.connect(POSTGRES_DSN) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (str(tenant_id),),
            )
            connection.execute(
                "DELETE FROM platform.audit_log WHERE tenant_id=%s",
                (tenant_id,),
            )
            connection.execute(
                "DELETE FROM platform.tenant WHERE id IN (%s,%s)",
                (tenant_id, foreign_tenant_id),
            )
