from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

from geo_platform.tenancy.ids import new_pub_id

from .export import artifact_sha256, build_metrics_csv_zip, build_metrics_xlsx
from .schemas import (
    ContributionPageView,
    DecisionOverrideRequest,
    DecisionOverrideView,
    ExportView,
    JobView,
    MetricCatalogView,
    MetricSnapshotDetailView,
    PublicationView,
    PublishRequest,
    QueryContributionPageView,
    RecomputeRequest,
    SemanticDecisionDetailView,
    SemanticEventDetailView,
    SnapshotRequest,
    SnapshotRequestAccepted,
    SnapshotSetView,
)


class MetricsV2Conflict(RuntimeError):
    pass


class MetricsV2Invalid(ValueError):
    pass


class MetricsV2RepositoryProtocol(Protocol):
    def catalog(self) -> list[dict[str, Any]]: ...

    def current_snapshot_set(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_snapshot_set(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_snapshot(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_query_contributions(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_contributions(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_semantic_event(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_semantic_decision(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_snapshot_job(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_decision_job(self, **kwargs: Any) -> dict[str, Any]: ...

    def request_snapshot(self, **kwargs: Any) -> dict[str, Any]: ...

    def publish_snapshot_set_cas(self, **kwargs: Any) -> dict[str, Any]: ...

    def request_recompute(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_override(self, **kwargs: Any) -> dict[str, Any]: ...

    def export_bundle(self, **kwargs: Any) -> dict[str, Any]: ...


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _as_dict(value: Mapping[str, Any] | object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("metrics_v2_repository_invalid_result")


class MetricsV2Service:
    """Application boundary for immutable metric snapshots.

    The service only validates authorization-scoped selections and delegates
    persistence.  It intentionally has no reference to ``MetricSnapshotEngine``
    or any model client, which is enforced by architecture tests.
    """

    def __init__(self, *, repository: MetricsV2RepositoryProtocol) -> None:
        self.repository = repository

    def catalog(self) -> MetricCatalogView:
        definitions = self.repository.catalog()
        return MetricCatalogView.model_validate(
            {"schema_version": "metric-catalog-v2", "definitions": definitions}
        )

    def current_snapshot_set(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: str | None,
        end: str | None,
        models: tuple[str, ...],
        regions: tuple[str, ...],
        modes: tuple[str, ...],
        focal_entity_ids: tuple[str, ...],
        publication_channel: str = "official",
    ) -> SnapshotSetView:
        result = self.repository.current_snapshot_set(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            start=start,
            end=end,
            models=models,
            regions=regions,
            modes=modes,
            focal_entity_ids=focal_entity_ids,
            publication_channel=publication_channel,
        )
        return SnapshotSetView.model_validate(result)

    def request_snapshot(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        request: SnapshotRequest,
        requested_by: str,
    ) -> SnapshotRequestAccepted:
        scope = {
            "tenant_pub_id": tenant_pub_id,
            "project_pub_id": project_pub_id,
            "window": request.window.model_dump(mode="json"),
            "filters": request.filters.model_dump(mode="json"),
            "focal_entity_ids": request.focal_entity_ids,
            "aggregation_method": request.aggregation_method,
            "publication_channel": request.publication_channel,
        }
        scope_hash = _canonical_hash(scope)
        idempotency_key = request.idempotency_key or scope_hash
        result = self.repository.request_snapshot(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            scope=scope,
            scope_hash=scope_hash,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )
        document = {"schema_version": "metric-snapshot-request-v2", **_as_dict(result)}
        document.setdefault("scope_hash", scope_hash)
        return SnapshotRequestAccepted.model_validate(document)

    def snapshot_job(self, *, tenant_pub_id: str, job_pub_id: str) -> JobView:
        document = {
            "schema_version": "metric-job-v2",
            **self.repository.get_snapshot_job(tenant_pub_id=tenant_pub_id, job_pub_id=job_pub_id),
        }
        return JobView.model_validate(document)

    def decision_job(self, *, tenant_pub_id: str, job_pub_id: str) -> JobView:
        document = {
            "schema_version": "semantic-decision-job-v2",
            **self.repository.get_decision_job(tenant_pub_id=tenant_pub_id, job_pub_id=job_pub_id),
        }
        return JobView.model_validate(document)

    def snapshot_set(self, *, tenant_pub_id: str, set_pub_id: str) -> SnapshotSetView:
        return SnapshotSetView.model_validate(
            self.repository.get_snapshot_set(tenant_pub_id=tenant_pub_id, set_pub_id=set_pub_id)
        )

    def snapshot(self, *, tenant_pub_id: str, snapshot_pub_id: str) -> MetricSnapshotDetailView:
        return MetricSnapshotDetailView.model_validate(
            self.repository.get_snapshot(
                tenant_pub_id=tenant_pub_id, snapshot_pub_id=snapshot_pub_id
            )
        )

    def query_contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None,
        limit: int,
        query: str | None,
    ) -> QueryContributionPageView:
        return QueryContributionPageView.model_validate(
            self.repository.list_query_contributions(
                tenant_pub_id=tenant_pub_id,
                snapshot_pub_id=snapshot_pub_id,
                cursor=cursor,
                limit=limit,
                query=query,
            )
        )

    def contributions(
        self,
        *,
        tenant_pub_id: str,
        snapshot_pub_id: str,
        cursor: str | None,
        limit: int,
        eligibility_status: str | None,
        reason_code: str | None,
        query: str | None,
        model: str | None,
        region: str | None,
        mode: str | None,
        hit: bool | None,
    ) -> ContributionPageView:
        return ContributionPageView.model_validate(
            self.repository.list_contributions(
                tenant_pub_id=tenant_pub_id,
                snapshot_pub_id=snapshot_pub_id,
                cursor=cursor,
                limit=limit,
                eligibility_status=eligibility_status,
                reason_code=reason_code,
                query=query,
                model=model,
                region=region,
                mode=mode,
                hit=hit,
            )
        )

    def semantic_event(self, *, tenant_pub_id: str, event_pub_id: str) -> SemanticEventDetailView:
        return SemanticEventDetailView.model_validate(
            self.repository.get_semantic_event(
                tenant_pub_id=tenant_pub_id, event_pub_id=event_pub_id
            )
        )

    def semantic_decision(
        self, *, tenant_pub_id: str, decision_pub_id: str
    ) -> SemanticDecisionDetailView:
        return SemanticDecisionDetailView.model_validate(
            self.repository.get_semantic_decision(
                tenant_pub_id=tenant_pub_id, decision_pub_id=decision_pub_id
            )
        )

    def publish(
        self,
        *,
        tenant_pub_id: str,
        set_pub_id: str,
        request: PublishRequest,
        published_by: str,
    ) -> PublicationView:
        try:
            result = self.repository.publish_snapshot_set_cas(
                tenant_pub_id=tenant_pub_id,
                set_pub_id=set_pub_id,
                publication_channel=request.publication_channel,
                expected_generation=request.expected_generation,
                expected_snapshot_set_hash=request.expected_snapshot_set_hash,
                published_by=published_by,
            )
        except RuntimeError as exc:
            raise MetricsV2Conflict(str(exc)) from exc
        return PublicationView.model_validate({"schema_version": "metric-publication-v2", **result})

    def recompute(
        self,
        *,
        tenant_pub_id: str,
        request: RecomputeRequest,
        requested_by: str,
    ) -> JobView:
        result = self.repository.request_recompute(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=request.project_pub_id,
            window=request.window.model_dump(mode="json"),
            focal_entity_ids=request.focal_entity_ids,
            trigger_reason=request.trigger_reason,
            idempotency_key=request.idempotency_key,
            requested_by=requested_by,
        )
        return JobView.model_validate({"schema_version": "metric-job-v2", **result})

    def override_decision(
        self,
        *,
        tenant_pub_id: str,
        decision_pub_id: str,
        request: DecisionOverrideRequest,
        actor_pub_id: str,
    ) -> DecisionOverrideView:
        try:
            result = self.repository.create_override(
                tenant_pub_id=tenant_pub_id,
                project_pub_id=request.project_pub_id,
                decision_pub_id=decision_pub_id,
                result=request.result,
                rationale_summary=request.rationale_summary,
                reason_codes=request.reason_codes,
                expected_decision_hash=request.expected_decision_hash,
                actor_pub_id=actor_pub_id,
            )
        except RuntimeError as exc:
            raise MetricsV2Conflict(str(exc)) from exc
        except ValueError as exc:
            raise MetricsV2Invalid(str(exc)) from exc
        return DecisionOverrideView.model_validate(
            {"schema_version": "semantic-decision-override-v2", **result}
        )

    def render_export(
        self,
        *,
        tenant_pub_id: str,
        set_pub_id: str,
        export_format: str,
    ) -> tuple[bytes, str, str]:
        bundle = self.repository.export_bundle(
            tenant_pub_id=tenant_pub_id,
            set_pub_id=set_pub_id,
        )
        if export_format == "xlsx":
            payload = build_metrics_xlsx(bundle)
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif export_format == "csv_zip":
            payload = build_metrics_csv_zip(bundle)
            mime_type = "application/zip"
        else:
            raise MetricsV2Invalid("invalid_metric_export_format")
        return payload, mime_type, artifact_sha256(payload)

    @staticmethod
    def completed_export_view(
        *,
        set_pub_id: str,
        export_format: str,
        artifact_hash: str,
        download_url: str,
    ) -> ExportView:
        return ExportView.model_validate(
            {
                "schema_version": "metric-export-v2",
                "export_pub_id": new_pub_id("mxe"),
                "snapshot_set_pub_id": set_pub_id,
                "status": "succeeded",
                "format": export_format,
                "artifact_hash": artifact_hash,
                "download_url": download_url,
                "expires_at": datetime.now(UTC),
            }
        )


__all__ = [
    "MetricsV2Conflict",
    "MetricsV2Invalid",
    "MetricsV2RepositoryProtocol",
    "MetricsV2Service",
]
