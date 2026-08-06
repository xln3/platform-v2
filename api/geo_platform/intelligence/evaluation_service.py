from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from domain.evidence.dlp import assert_secret_free
from domain.intelligence.evaluation import (
    REQUIRED_EXPLANATION_FIELDS,
    EvaluationCase,
    EvaluationMetrics,
    evaluate,
)

from ..tenancy.ids import new_pub_id
from ..tenancy.psycopg import tenant_connection

ADMISSION_POLICY_VERSION = "anti-geo-admission-v1"
MIN_PRECISION = Decimal("0.80")
MIN_RECALL = Decimal("0.80")
MAX_FALSE_POSITIVE_RATE = Decimal("0.10")
MAX_BRIER_SCORE = Decimal("0.20")
MAX_EXPECTED_CALIBRATION_ERROR = Decimal("0.10")
MIN_EXPLANATION_COMPLETENESS = Decimal("1")


@dataclass(frozen=True, slots=True)
class DatasetCaseInput:
    case_digest: str
    propagation_cluster_digest: str
    actual_positive: bool


@dataclass(frozen=True, slots=True)
class PredictionInput:
    case_digest: str
    probability: Decimal
    predicted_positive: bool
    explanation_fields: frozenset[str]


def _canonical_digest(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _operation_hash(idempotency_key: str) -> str:
    if len(idempotency_key) < 16 or len(idempotency_key) > 200:
        raise ValueError("idempotency_key_invalid")
    assert_secret_free(idempotency_key)
    return sha256(idempotency_key.encode()).hexdigest()


def _safe_text(value: str, *, code: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(code)
    assert_secret_free(normalized)
    return normalized


def _metrics_dict(metrics: EvaluationMetrics) -> dict[str, Decimal | int | str | None]:
    return {
        "precision": metrics.precision,
        "recall": metrics.recall,
        "false_positive_rate": metrics.false_positive_rate,
        "brier_score": metrics.brier_score,
        "expected_calibration_error": metrics.expected_calibration_error,
        "explanation_completeness_rate": metrics.explanation_completeness_rate,
        "sample_count": metrics.sample_count,
        "positive_count": metrics.positive_count,
        "negative_count": metrics.negative_count,
        "dataset_version": metrics.dataset_version,
        "scorer_version": metrics.scorer_version,
        "evaluation_sha256": metrics.dataset_sha256,
    }


def _admission_checks(metrics: EvaluationMetrics) -> dict[str, bool]:
    return {
        "precision": metrics.precision is not None and metrics.precision >= MIN_PRECISION,
        "recall": metrics.recall is not None and metrics.recall >= MIN_RECALL,
        "false_positive_rate": (
            metrics.false_positive_rate is not None
            and metrics.false_positive_rate <= MAX_FALSE_POSITIVE_RATE
        ),
        "brier_score": metrics.brier_score <= MAX_BRIER_SCORE,
        "expected_calibration_error": (
            metrics.expected_calibration_error <= MAX_EXPECTED_CALIBRATION_ERROR
        ),
        "explanation_completeness": (
            metrics.explanation_completeness_rate >= MIN_EXPLANATION_COMPLETENESS
        ),
    }


def _append_audit(
    connection: psycopg.Connection[Any],
    *,
    tenant_pub_id: str,
    actor_pub_id: str,
    action: str,
    resource_type: str,
    resource_pub_id: str,
    receipt: dict[str, object],
) -> None:
    tenant = connection.execute(
        "SELECT id AS tenant_id FROM platform.tenant WHERE pub_id=%s",
        (tenant_pub_id,),
    ).fetchone()
    if tenant is None:
        raise RuntimeError("evaluation_audit_tenant_not_found")
    connection.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant["tenant_id"]),),
    )
    connection.execute(
        """
        INSERT INTO platform.audit_log (
          id,pub_id,tenant_id,actor_pub_id,action,resource_type,
          resource_pub_id,receipt,occurred_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
        """,
        (
            uuid4(),
            new_pub_id("aud"),
            tenant["tenant_id"],
            actor_pub_id,
            action,
            resource_type,
            resource_pub_id,
            json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        ),
    )


class EvaluationAdmissionService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def register_dataset(
        self,
        *,
        tenant_pub_id: str,
        actor_pub_id: str,
        idempotency_key: str,
        version: str,
        source_artifact_pub_id: str,
        source_artifact_sha256: str,
        label_policy_version: str,
        labeler_count: int,
        cases: tuple[DatasetCaseInput, ...],
    ) -> dict[str, Any]:
        version = _safe_text(version, code="dataset_version_required")
        label_policy_version = _safe_text(
            label_policy_version, code="label_policy_version_required"
        )
        source_artifact_pub_id = _safe_text(
            source_artifact_pub_id, code="dataset_source_artifact_required"
        )
        if len(cases) < 20 or len(cases) > 10_000:
            raise ValueError("dataset_case_count_invalid")
        if labeler_count < 2 or labeler_count > 100:
            raise ValueError("dataset_labeler_count_invalid")
        case_digests = [item.case_digest for item in cases]
        cluster_digests = [item.propagation_cluster_digest for item in cases]
        if len(set(case_digests)) != len(case_digests):
            raise ValueError("dataset_case_duplicate")
        if len(set(cluster_digests)) != len(cluster_digests):
            raise ValueError("dataset_cluster_duplicate")
        positive_count = sum(item.actual_positive for item in cases)
        if positive_count == 0 or positive_count == len(cases):
            raise ValueError("dataset_requires_both_labels")
        canonical_cases = [
            {
                "actual_positive": item.actual_positive,
                "case_digest": item.case_digest,
                "propagation_cluster_digest": item.propagation_cluster_digest,
            }
            for item in sorted(cases, key=lambda item: item.case_digest)
        ]
        contract = {
            "cases": canonical_cases,
            "label_policy_version": label_policy_version,
            "labeler_count": labeler_count,
            "source_artifact_pub_id": source_artifact_pub_id,
            "source_artifact_sha256": source_artifact_sha256,
            "version": version,
        }
        contract_hash = _canonical_digest(contract)
        dataset_hash = _canonical_digest(
            {
                "cases": canonical_cases,
                "label_policy_version": label_policy_version,
                "source_artifact_pub_id": source_artifact_pub_id,
                "source_artifact_sha256": source_artifact_sha256,
                "version": version,
            }
        )
        operation_hash = _operation_hash(idempotency_key)
        dataset_pub_id = new_pub_id("dset")
        try:
            with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"evaluation-dataset|{tenant_pub_id}|{operation_hash}",),
                )
                existing = connection.execute(
                    """
                    SELECT pub_id,version,dataset_sha256,registration_contract_hash,state,
                           case_count,positive_count,labeler_count,submitted_at,approved_at
                    FROM intelligence.evaluation_dataset
                    WHERE tenant_pub_id=%s AND registration_operation_hash=%s
                    """,
                    (tenant_pub_id, operation_hash),
                ).fetchone()
                if existing is not None:
                    if existing["registration_contract_hash"] != contract_hash:
                        raise ValueError("dataset_idempotency_conflict")
                    return dict(existing)
                source_artifact = connection.execute(
                    """
                    SELECT sha256,kind,dlp_findings
                    FROM evidence.evidence_asset
                    WHERE tenant_pub_id=%s AND pub_id=%s AND deleted_at IS NULL
                    """,
                    (tenant_pub_id, source_artifact_pub_id),
                ).fetchone()
                if source_artifact is None:
                    raise LookupError("dataset_source_artifact_not_found")
                if source_artifact["sha256"] != source_artifact_sha256:
                    raise ValueError("dataset_source_artifact_hash_mismatch")
                if source_artifact["kind"] != "anti_geo_calibration_dataset":
                    raise ValueError("dataset_source_artifact_kind_invalid")
                if source_artifact["dlp_findings"]:
                    raise ValueError("dataset_source_artifact_dlp_blocked")
                row = connection.execute(
                    """
                    INSERT INTO intelligence.evaluation_dataset (
                      id,pub_id,tenant_pub_id,version,source_artifact_pub_id,
                      source_artifact_sha256,
                      label_policy_version,labeler_count,case_count,positive_count,
                      dataset_sha256,registration_operation_hash,
                      registration_contract_hash,submitted_by_pub_id
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING pub_id,version,dataset_sha256,registration_contract_hash,state,
                              case_count,positive_count,labeler_count,submitted_at,approved_at
                    """,
                    (
                        uuid4(),
                        dataset_pub_id,
                        tenant_pub_id,
                        version,
                        source_artifact_pub_id,
                        source_artifact_sha256,
                        label_policy_version,
                        labeler_count,
                        len(cases),
                        positive_count,
                        dataset_hash,
                        operation_hash,
                        contract_hash,
                        actor_pub_id,
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("dataset_registration_unavailable")
                if row["registration_contract_hash"] != contract_hash:
                    raise ValueError("dataset_idempotency_conflict")
                dataset_pub_id = str(row["pub_id"])
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO intelligence.evaluation_dataset_case (
                          tenant_pub_id,dataset_pub_id,case_digest,
                          propagation_cluster_digest,actual_positive
                        ) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (tenant_pub_id,dataset_pub_id,case_digest) DO NOTHING
                        """,
                        (
                            (
                                tenant_pub_id,
                                dataset_pub_id,
                                item.case_digest,
                                item.propagation_cluster_digest,
                                item.actual_positive,
                            )
                            for item in cases
                        ),
                    )
                persisted = connection.execute(
                    """
                    SELECT count(*) AS case_count,
                           count(*) FILTER (WHERE actual_positive) AS positive_count,
                           count(DISTINCT propagation_cluster_digest) AS cluster_count
                    FROM intelligence.evaluation_dataset_case
                    WHERE tenant_pub_id=%s AND dataset_pub_id=%s
                    """,
                    (tenant_pub_id, dataset_pub_id),
                ).fetchone()
                if persisted is None or (
                    int(persisted["case_count"]),
                    int(persisted["positive_count"]),
                    int(persisted["cluster_count"]),
                ) != (len(cases), positive_count, len(cases)):
                    raise ValueError("dataset_persisted_case_drift")
                _append_audit(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    actor_pub_id=actor_pub_id,
                    action="intelligence.evaluation_dataset.registered",
                    resource_type="evaluation_dataset",
                    resource_pub_id=dataset_pub_id,
                    receipt={
                        "case_count": len(cases),
                        "dataset_sha256": dataset_hash,
                        "labeler_count": labeler_count,
                        "positive_count": positive_count,
                        "source_artifact_sha256": source_artifact_sha256,
                    },
                )
                return dict(row)
        except psycopg.errors.UniqueViolation as error:
            raise ValueError("dataset_version_or_hash_exists") from error

    def approve_dataset(
        self,
        *,
        tenant_pub_id: str,
        actor_pub_id: str,
        dataset_pub_id: str,
        rationale: str,
    ) -> dict[str, Any]:
        rationale = _safe_text(rationale, code="dataset_approval_rationale_required")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT dataset.pub_id,dataset.version,dataset.dataset_sha256,dataset.state,
                       dataset.case_count,dataset.positive_count,dataset.labeler_count,
                       dataset.submitted_at,dataset.approved_at,
                       dataset.submitted_by_pub_id,dataset.approved_by_pub_id,
                       dataset.approval_rationale,source.deleted_at AS source_deleted_at
                FROM intelligence.evaluation_dataset dataset
                JOIN evidence.evidence_asset source
                  ON source.pub_id=dataset.source_artifact_pub_id
                 AND source.tenant_pub_id=dataset.tenant_pub_id
                WHERE dataset.tenant_pub_id=%s AND dataset.pub_id=%s
                FOR UPDATE
                """,
                (tenant_pub_id, dataset_pub_id),
            ).fetchone()
            if row is None:
                raise LookupError("evaluation_dataset_not_found")
            if row["submitted_by_pub_id"] == actor_pub_id:
                raise PermissionError("dataset_independent_reviewer_required")
            if row["source_deleted_at"] is not None:
                raise ValueError("dataset_source_artifact_deleted")
            if row["state"] == "approved":
                if (
                    row["approved_by_pub_id"] == actor_pub_id
                    and row["approval_rationale"] == rationale
                ):
                    return dict(row)
                raise ValueError("dataset_already_approved")
            if row["state"] != "draft":
                raise ValueError("dataset_not_approvable")
            approved = connection.execute(
                """
                UPDATE intelligence.evaluation_dataset
                SET state='approved',approved_by_pub_id=%s,approval_rationale=%s,
                    approved_at=now()
                WHERE tenant_pub_id=%s AND pub_id=%s
                RETURNING pub_id,version,dataset_sha256,state,case_count,positive_count,
                          labeler_count,submitted_at,approved_at
                """,
                (actor_pub_id, rationale, tenant_pub_id, dataset_pub_id),
            ).fetchone()
            if approved is None:
                raise RuntimeError("dataset_approval_unavailable")
            _append_audit(
                connection,
                tenant_pub_id=tenant_pub_id,
                actor_pub_id=actor_pub_id,
                action="intelligence.evaluation_dataset.approved",
                resource_type="evaluation_dataset",
                resource_pub_id=dataset_pub_id,
                receipt={
                    "dataset_sha256": approved["dataset_sha256"],
                    "independent_reviewer": True,
                },
            )
            return dict(approved)

    def list_datasets(
        self, *, tenant_pub_id: str, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT pub_id,version,dataset_sha256,state,case_count,positive_count,
                       labeler_count,submitted_at,approved_at
                FROM intelligence.evaluation_dataset
                WHERE tenant_pub_id=%s AND (%s::text IS NULL OR pub_id>%s::text)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [dict(row) for row in rows[:limit]], len(rows) > limit

    def evaluate_dataset(
        self,
        *,
        tenant_pub_id: str,
        actor_pub_id: str,
        dataset_pub_id: str,
        idempotency_key: str,
        scorer_version: str,
        decision_threshold: Decimal,
        calibration_bins: int,
        training_propagation_cluster_digests: tuple[str, ...],
        predictions: tuple[PredictionInput, ...],
    ) -> dict[str, Any]:
        scorer_version = _safe_text(scorer_version, code="scorer_version_required")
        operation_hash = _operation_hash(idempotency_key)
        prediction_by_case = {item.case_digest: item for item in predictions}
        if len(prediction_by_case) != len(predictions):
            raise ValueError("evaluation_prediction_duplicate")
        if len(training_propagation_cluster_digests) > 50_000:
            raise ValueError("evaluation_training_cluster_count_invalid")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in training_propagation_cluster_digests
        ):
            raise ValueError("evaluation_training_cluster_digest_invalid")
        if len(set(training_propagation_cluster_digests)) != len(
            training_propagation_cluster_digests
        ):
            raise ValueError("evaluation_training_cluster_duplicate")
        training_cluster_manifest_sha256 = _canonical_digest(
            sorted(training_propagation_cluster_digests)
        )
        if any(
            not item.explanation_fields.issubset(REQUIRED_EXPLANATION_FIELDS)
            for item in predictions
        ):
            raise ValueError("evaluation_explanation_field_invalid")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            dataset = connection.execute(
                """
                SELECT pub_id,version,dataset_sha256,state
                FROM intelligence.evaluation_dataset
                WHERE tenant_pub_id=%s AND pub_id=%s
                """,
                (tenant_pub_id, dataset_pub_id),
            ).fetchone()
            if dataset is None:
                raise LookupError("evaluation_dataset_not_found")
            if dataset["state"] != "approved":
                raise ValueError("evaluation_dataset_not_approved")
            labels = connection.execute(
                """
                SELECT case_digest,propagation_cluster_digest,actual_positive
                FROM intelligence.evaluation_dataset_case
                WHERE tenant_pub_id=%s AND dataset_pub_id=%s
                ORDER BY case_digest
                """,
                (tenant_pub_id, dataset_pub_id),
            ).fetchall()
            if set(prediction_by_case) != {str(row["case_digest"]) for row in labels}:
                raise ValueError("evaluation_prediction_coverage_mismatch")
            holdout_clusters = {str(row["propagation_cluster_digest"]) for row in labels}
            if holdout_clusters.intersection(training_propagation_cluster_digests):
                raise ValueError("evaluation_training_holdout_cluster_overlap")
            cases = tuple(
                EvaluationCase(
                    propagation_cluster_id=str(row["propagation_cluster_digest"]),
                    probability=prediction_by_case[str(row["case_digest"])].probability,
                    actual_positive=bool(row["actual_positive"]),
                    predicted_positive=prediction_by_case[
                        str(row["case_digest"])
                    ].predicted_positive,
                    explanation_fields_present=prediction_by_case[
                        str(row["case_digest"])
                    ].explanation_fields,
                )
                for row in labels
            )
            metrics = evaluate(
                cases,
                dataset_version=str(dataset["version"]),
                scorer_version=scorer_version,
                decision_threshold=decision_threshold,
                calibration_bins=calibration_bins,
            )
            checks = _admission_checks(metrics)
            contract = {
                "calibration_bins": calibration_bins,
                "dataset_pub_id": dataset_pub_id,
                "dataset_sha256": dataset["dataset_sha256"],
                "decision_threshold": str(decision_threshold),
                "training_cluster_count": len(training_propagation_cluster_digests),
                "training_cluster_manifest_sha256": training_cluster_manifest_sha256,
                "predictions": [
                    {
                        "case_digest": item.case_digest,
                        "explanation_fields": sorted(item.explanation_fields),
                        "predicted_positive": item.predicted_positive,
                        "probability": str(item.probability),
                    }
                    for item in sorted(predictions, key=lambda item: item.case_digest)
                ],
                "scorer_version": scorer_version,
            }
            contract_hash = _canonical_digest(contract)
            run_pub_id = new_pub_id("eval")
            row = connection.execute(
                """
                INSERT INTO intelligence.evaluation_run (
                  id,pub_id,tenant_pub_id,dataset_pub_id,scorer_version,
                  decision_threshold,calibration_bins,training_cluster_manifest_sha256,
                  training_cluster_count,sample_count,precision,recall,
                  false_positive_rate,brier_score,expected_calibration_error,
                  explanation_completeness_rate,evaluation_sha256,
                  admission_policy_version,admission_checks,admission_passed,
                  operation_hash,contract_hash,created_by_pub_id
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s
                )
                ON CONFLICT (tenant_pub_id,operation_hash)
                DO UPDATE SET pub_id=intelligence.evaluation_run.pub_id
                RETURNING pub_id,dataset_pub_id,scorer_version,decision_threshold,
                          calibration_bins,training_cluster_manifest_sha256,
                          training_cluster_count,sample_count,precision,recall,false_positive_rate,
                          brier_score,expected_calibration_error,
                          explanation_completeness_rate,evaluation_sha256,
                          admission_policy_version,admission_checks,admission_passed,
                          contract_hash,created_at,(xmax=0) AS inserted
                """,
                (
                    uuid4(),
                    run_pub_id,
                    tenant_pub_id,
                    dataset_pub_id,
                    scorer_version,
                    decision_threshold,
                    calibration_bins,
                    training_cluster_manifest_sha256,
                    len(training_propagation_cluster_digests),
                    metrics.sample_count,
                    metrics.precision,
                    metrics.recall,
                    metrics.false_positive_rate,
                    metrics.brier_score,
                    metrics.expected_calibration_error,
                    metrics.explanation_completeness_rate,
                    metrics.dataset_sha256,
                    ADMISSION_POLICY_VERSION,
                    json.dumps(checks, sort_keys=True, separators=(",", ":")),
                    all(checks.values()),
                    operation_hash,
                    contract_hash,
                    actor_pub_id,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("evaluation_run_unavailable")
            if row["contract_hash"] != contract_hash:
                raise ValueError("evaluation_idempotency_conflict")
            run_pub_id = str(row["pub_id"])
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO intelligence.evaluation_case_result (
                      tenant_pub_id,evaluation_run_pub_id,case_digest,actual_positive,
                      probability,predicted_positive,explanation_fields
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_pub_id,evaluation_run_pub_id,case_digest)
                    DO NOTHING
                    """,
                    (
                        (
                            tenant_pub_id,
                            run_pub_id,
                            str(label["case_digest"]),
                            bool(label["actual_positive"]),
                            prediction_by_case[str(label["case_digest"])].probability,
                            prediction_by_case[str(label["case_digest"])].predicted_positive,
                            sorted(
                                prediction_by_case[str(label["case_digest"])].explanation_fields
                            ),
                        )
                        for label in labels
                    ),
                )
            persisted_count = connection.execute(
                """
                SELECT count(*)
                FROM intelligence.evaluation_case_result
                WHERE tenant_pub_id=%s AND evaluation_run_pub_id=%s
                """,
                (tenant_pub_id, run_pub_id),
            ).fetchone()
            if persisted_count is None or int(persisted_count["count"]) != len(labels):
                raise ValueError("evaluation_result_persistence_drift")
            if row["inserted"]:
                _append_audit(
                    connection,
                    tenant_pub_id=tenant_pub_id,
                    actor_pub_id=actor_pub_id,
                    action="intelligence.evaluation_run.created",
                    resource_type="evaluation_run",
                    resource_pub_id=run_pub_id,
                    receipt={
                        "admission_passed": all(checks.values()),
                        "admission_policy_version": ADMISSION_POLICY_VERSION,
                        "dataset_pub_id": dataset_pub_id,
                        "evaluation_sha256": metrics.dataset_sha256,
                        "sample_count": metrics.sample_count,
                        "training_cluster_count": len(training_propagation_cluster_digests),
                        "training_cluster_manifest_sha256": (training_cluster_manifest_sha256),
                    },
                )
            result = dict(row)
            result.pop("inserted", None)
            result["metrics"] = _metrics_dict(metrics)
            return result

    def admit_model(
        self,
        *,
        tenant_pub_id: str,
        actor_pub_id: str,
        evaluation_run_pub_id: str,
        idempotency_key: str,
        rationale: str,
    ) -> dict[str, Any]:
        rationale = _safe_text(rationale, code="model_admission_rationale_required")
        operation_hash = _operation_hash(idempotency_key)
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            run = connection.execute(
                """
                SELECT run.pub_id,run.scorer_version,run.admission_passed,
                       run.created_by_pub_id,run.evaluation_sha256,
                       dataset.state AS dataset_state
                FROM intelligence.evaluation_run run
                JOIN intelligence.evaluation_dataset dataset
                  ON dataset.pub_id=run.dataset_pub_id
                 AND dataset.tenant_pub_id=run.tenant_pub_id
                WHERE run.tenant_pub_id=%s AND run.pub_id=%s
                """,
                (tenant_pub_id, evaluation_run_pub_id),
            ).fetchone()
            if run is None:
                raise LookupError("evaluation_run_not_found")
            if not run["admission_passed"]:
                raise ValueError("evaluation_thresholds_not_met")
            if run["dataset_state"] != "approved":
                raise ValueError("evaluation_dataset_not_approved")
            if run["created_by_pub_id"] == actor_pub_id:
                raise PermissionError("model_independent_reviewer_required")
            contract_hash = _canonical_digest(
                {
                    "evaluation_run_pub_id": evaluation_run_pub_id,
                    "evaluation_sha256": run["evaluation_sha256"],
                    "rationale": rationale,
                    "scorer_version": run["scorer_version"],
                }
            )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"model-admission|{tenant_pub_id}|{operation_hash}",),
            )
            existing = connection.execute(
                """
                SELECT pub_id,evaluation_run_pub_id,scorer_version,state,contract_hash,
                       rationale,admitted_at,revoked_at
                FROM intelligence.model_admission
                WHERE tenant_pub_id=%s AND operation_hash=%s
                """,
                (tenant_pub_id, operation_hash),
            ).fetchone()
            if existing is not None:
                if existing["contract_hash"] != contract_hash:
                    raise ValueError("model_admission_idempotency_conflict")
                return dict(existing)
            try:
                row = connection.execute(
                    """
                    INSERT INTO intelligence.model_admission (
                      id,pub_id,tenant_pub_id,evaluation_run_pub_id,scorer_version,
                      operation_hash,contract_hash,admitted_by_pub_id,rationale
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING pub_id,evaluation_run_pub_id,scorer_version,state,
                              contract_hash,rationale,admitted_at,revoked_at
                    """,
                    (
                        uuid4(),
                        new_pub_id("madm"),
                        tenant_pub_id,
                        evaluation_run_pub_id,
                        run["scorer_version"],
                        operation_hash,
                        contract_hash,
                        actor_pub_id,
                        rationale,
                    ),
                ).fetchone()
            except psycopg.errors.UniqueViolation as error:
                raise ValueError("scorer_version_already_admitted") from error
            if row is None:
                raise RuntimeError("model_admission_unavailable")
            if row["contract_hash"] != contract_hash:
                raise ValueError("model_admission_idempotency_conflict")
            _append_audit(
                connection,
                tenant_pub_id=tenant_pub_id,
                actor_pub_id=actor_pub_id,
                action="intelligence.model_admission.created",
                resource_type="model_admission",
                resource_pub_id=str(row["pub_id"]),
                receipt={
                    "evaluation_run_pub_id": evaluation_run_pub_id,
                    "evaluation_sha256": run["evaluation_sha256"],
                    "independent_reviewer": True,
                    "scorer_version": run["scorer_version"],
                },
            )
            return dict(row)

    def list_evaluation_runs(
        self, *, tenant_pub_id: str, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT run.pub_id,run.dataset_pub_id,run.scorer_version,
                       run.decision_threshold,run.calibration_bins,
                       run.training_cluster_manifest_sha256,run.training_cluster_count,
                       run.sample_count,
                       run.precision,run.recall,run.false_positive_rate,run.brier_score,
                       run.expected_calibration_error,
                       run.explanation_completeness_rate,run.evaluation_sha256,
                       run.admission_policy_version,run.admission_checks,
                       run.admission_passed,run.created_at,dataset.version AS dataset_version,
                       (
                         SELECT count(*)
                         FROM intelligence.evaluation_case_result result
                         WHERE result.tenant_pub_id=run.tenant_pub_id
                           AND result.evaluation_run_pub_id=run.pub_id
                           AND result.actual_positive
                       ) AS positive_count,
                       (
                         SELECT admission.state
                         FROM intelligence.model_admission admission
                         WHERE admission.tenant_pub_id=run.tenant_pub_id
                           AND admission.evaluation_run_pub_id=run.pub_id
                         ORDER BY admission.admitted_at DESC,admission.pub_id DESC
                         LIMIT 1
                       ) AS model_admission_state
                FROM intelligence.evaluation_run run
                JOIN intelligence.evaluation_dataset dataset
                  ON dataset.tenant_pub_id=run.tenant_pub_id
                 AND dataset.pub_id=run.dataset_pub_id
                WHERE run.tenant_pub_id=%s
                  AND (%s::text IS NULL OR run.pub_id>%s::text)
                ORDER BY run.pub_id
                LIMIT %s
                """,
                (tenant_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        data: list[dict[str, Any]] = []
        for row in rows[:limit]:
            positive_count = int(row["positive_count"])
            data.append(
                {
                    "pub_id": row["pub_id"],
                    "dataset_pub_id": row["dataset_pub_id"],
                    "scorer_version": row["scorer_version"],
                    "decision_threshold": row["decision_threshold"],
                    "calibration_bins": row["calibration_bins"],
                    "training_cluster_manifest_sha256": row["training_cluster_manifest_sha256"],
                    "training_cluster_count": row["training_cluster_count"],
                    "sample_count": row["sample_count"],
                    "admission_policy_version": row["admission_policy_version"],
                    "admission_checks": row["admission_checks"],
                    "admission_passed": row["admission_passed"],
                    "model_admission_state": row["model_admission_state"],
                    "metrics": {
                        "precision": row["precision"],
                        "recall": row["recall"],
                        "false_positive_rate": row["false_positive_rate"],
                        "brier_score": row["brier_score"],
                        "expected_calibration_error": row["expected_calibration_error"],
                        "explanation_completeness_rate": row["explanation_completeness_rate"],
                        "sample_count": row["sample_count"],
                        "positive_count": positive_count,
                        "negative_count": int(row["sample_count"]) - positive_count,
                        "dataset_version": row["dataset_version"],
                        "scorer_version": row["scorer_version"],
                        "evaluation_sha256": row["evaluation_sha256"],
                    },
                    "created_at": row["created_at"],
                }
            )
        return data, len(rows) > limit

    def list_model_admissions(
        self, *, tenant_pub_id: str, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT pub_id,evaluation_run_pub_id,scorer_version,state,rationale,
                       admitted_at,revoked_at
                FROM intelligence.model_admission
                WHERE tenant_pub_id=%s AND (%s::text IS NULL OR pub_id>%s::text)
                ORDER BY pub_id LIMIT %s
                """,
                (tenant_pub_id, cursor, cursor, limit + 1),
            ).fetchall()
        return [dict(row) for row in rows[:limit]], len(rows) > limit


def required_explanation_fields() -> tuple[str, ...]:
    return tuple(sorted(REQUIRED_EXPLANATION_FIELDS))
