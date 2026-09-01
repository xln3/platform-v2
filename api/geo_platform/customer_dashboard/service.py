from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

import psycopg
from psycopg.rows import dict_row

from domain.collection.answer_content import project_answer_content
from domain.metrics.customer import (
    CustomerAnswerFact,
    CustomerCitationFact,
    CustomerRiskFact,
    CustomerSourceAuditFact,
    assert_customer_projection_safe,
    build_customer_metric_bundle,
    infer_competitor_mentions,
    infer_recommendation,
    is_own_site,
    own_site_host,
)

from ..metrics_v2.repository import MetricsV2Repository
from ..metrics_v2.schemas import MetricSnapshotView
from .schemas import CustomerDashboardV2View, CustomerMetricTraceV2View

CUSTOMER_METRIC_NAMES_V2: dict[tuple[str, str], tuple[str, ...]] = {
    (
        "ai_impression",
        "brand_neutral",
    ): ("ai_impression_neutral_spontaneous_association_rate_v2",),
    ("ai_impression", "focal_named_only"): (),
    ("ai_impression", "other_brand_named"): (),
    ("ai_impression", "focal_named_with_others"): (),
    (
        "ai_recommendation",
        "brand_neutral",
    ): (
        "ai_recommendation_organic_mention_rate_v2",
        "ai_recommendation_organic_recommendation_rate_v2",
        "ai_recommendation_rankable_response_rate_v2",
        "ai_recommendation_organic_top1_visibility_rate_v2",
        "ai_recommendation_organic_top3_visibility_rate_v2",
        "ai_recommendation_organic_top5_visibility_rate_v2",
        "ai_recommendation_organic_top1_given_rankable_rate_v2",
        "ai_recommendation_organic_top3_given_rankable_rate_v2",
        "ai_recommendation_organic_top5_given_rankable_rate_v2",
        "ai_recommendation_mean_rank_given_target_ranked_v2",
        "ai_recommendation_entity_share_v2",
    ),
    (
        "ai_recommendation",
        "focal_named_only",
    ): (
        "prompted_recommendation_positive_rate_v2",
        "prompted_recommendation_conditional_rate_v2",
        "prompted_recommendation_negative_rate_v2",
        "prompted_recommendation_neutral_rate_v2",
    ),
    (
        "ai_recommendation",
        "other_brand_named",
    ): (
        "competitor_anchored_target_bring_in_rate_v2",
        "competitor_anchored_target_alternative_rate_v2",
    ),
    (
        "ai_recommendation",
        "focal_named_with_others",
    ): (
        "multibrand_pairwise_win_rate_v2",
        "multibrand_pairwise_tie_rate_v2",
        "multibrand_pairwise_loss_rate_v2",
        "multibrand_corecommendation_rate_v2",
    ),
}

_CUSTOMER_METRIC_LABELS_V2 = {
    "ai_impression_neutral_spontaneous_association_rate_v2": "品牌中性 AI 印象自发联想率",
    "ai_recommendation_organic_mention_rate_v2": "中性 AI 推荐自然提及率",
    "ai_recommendation_organic_recommendation_rate_v2": "中性 AI 推荐自然推荐率",
    "ai_recommendation_rankable_response_rate_v2": "中性 AI 推荐可排序回答率",
    "ai_recommendation_organic_top1_visibility_rate_v2": "中性 AI 推荐 Top1 可见率",
    "ai_recommendation_organic_top3_visibility_rate_v2": "中性 AI 推荐 Top3 可见率",
    "ai_recommendation_organic_top5_visibility_rate_v2": "中性 AI 推荐 Top5 可见率",
    "ai_recommendation_organic_top1_given_rankable_rate_v2": "可排序回答内 Top1 率",
    "ai_recommendation_organic_top3_given_rankable_rate_v2": "可排序回答内 Top3 率",
    "ai_recommendation_organic_top5_given_rankable_rate_v2": "可排序回答内 Top5 率",
    "ai_recommendation_mean_rank_given_target_ranked_v2": "目标有推荐排名时的平均名次",
    "ai_recommendation_entity_share_v2": "中性推荐实体份额",
    "prompted_recommendation_positive_rate_v2": "焦点品牌点名后正向推荐率",
    "prompted_recommendation_conditional_rate_v2": "焦点品牌点名后有条件推荐率",
    "prompted_recommendation_negative_rate_v2": "焦点品牌点名后负向推荐率",
    "prompted_recommendation_neutral_rate_v2": "焦点品牌点名后中性推荐率",
    "competitor_anchored_target_bring_in_rate_v2": "其他品牌点名后焦点品牌带出率",
    "competitor_anchored_target_alternative_rate_v2": "其他品牌点名后替代推荐率",
    "multibrand_pairwise_win_rate_v2": "多品牌同问两两胜出率",
    "multibrand_pairwise_tie_rate_v2": "多品牌同问两两持平率",
    "multibrand_pairwise_loss_rate_v2": "多品牌同问两两落后率",
    "multibrand_corecommendation_rate_v2": "多品牌同问共同推荐率",
}


class CustomerMetricsV2RepositoryProtocol(Protocol):
    def catalog(self) -> list[dict[str, Any]]: ...

    def current_snapshot_set(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_snapshot_set(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_snapshot(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_contributions(self, **kwargs: Any) -> dict[str, Any]: ...


@contextmanager
def _customer_connection(dsn: str, tenant_pub_id: str) -> Iterator[psycopg.Connection[Any]]:
    """Open one read transaction with both analytics and platform RLS selectors."""

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        tenant = connection.execute(
            "SELECT id FROM platform.tenant WHERE pub_id=%s", (tenant_pub_id,)
        ).fetchone()
        if tenant is None:
            raise LookupError("tenant_not_found")
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "set_config('app.tenant_pub_id', %s, true)",
            (str(tenant["id"]), tenant_pub_id),
        )
        yield connection


def _safe_competitor_ranks(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: rank
        for key, rank in value.items()
        if isinstance(key, str)
        and 0 < len(key) <= 200
        and isinstance(rank, int)
        and not isinstance(rank, bool)
        and 1 <= rank <= 10_000
    }


class CustomerDashboardV2Service:
    """Read-only customer projection over immutable metric snapshot sets.

    This boundary deliberately has no metric evaluator, heuristic classifier or
    model client.  Missing sets, metrics and definitions are surfaced as
    unavailable; an official customer read never falls back to V1 computation.
    """

    def __init__(
        self,
        *,
        dsn: str,
        repository: CustomerMetricsV2RepositoryProtocol | None = None,
    ) -> None:
        self.dsn = dsn
        self.repository = repository or MetricsV2Repository(dsn=dsn)

    def _brand_name(self, *, tenant_pub_id: str, project_pub_id: str) -> str:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            row = connection.execute(
                """
                SELECT p.name,b.name AS brand_name
                FROM platform.project p
                LEFT JOIN LATERAL (
                  SELECT name FROM platform.brand
                  WHERE project_id=p.id
                  ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                ) b ON true
                WHERE p.pub_id=%s
                """,
                (project_pub_id,),
            ).fetchone()
        if row is None:
            raise LookupError("project_not_found")
        return str(row["brand_name"] or row["name"]).strip() or "未命名品牌"

    @staticmethod
    def _validate_metric_names(
        *, business_view: str, exposure_role: str, metric_names: Sequence[str]
    ) -> tuple[str, ...]:
        normalized = tuple(metric_names)
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("invalid_customer_metric_names_v2")
        allowed = CUSTOMER_METRIC_NAMES_V2.get((business_view, exposure_role))
        if allowed is None or not allowed:
            raise ValueError("unsupported_customer_metric_cohort_v2")
        if any(name not in allowed for name in normalized):
            raise ValueError("customer_metric_outside_requested_cohort_v2")
        return normalized

    def _catalog(self) -> dict[tuple[str, str, str], Mapping[str, Any]]:
        return {
            (
                str(item["metric_name"]),
                str(item["metric_version"]),
                str(item["definition_hash"]),
            ): item
            for item in self.repository.catalog()
        }

    @staticmethod
    def _filter_values(values: Sequence[str], *, maximum_length: int) -> tuple[str, ...]:
        if len(values) > 100:
            raise ValueError("customer_dashboard_v2_filter_limit_exceeded")
        normalized = tuple(value.strip() for value in values)
        if any(not value or len(value) > maximum_length for value in normalized):
            raise ValueError("invalid_customer_dashboard_v2_filter")
        return tuple(sorted(set(normalized)))

    @staticmethod
    def _metric_card(
        *,
        raw_metric: Mapping[str, Any],
        definition: Mapping[str, Any],
        business_view: str,
        exposure_role: str,
        aggregation_method: str,
    ) -> dict[str, Any]:
        metric = MetricSnapshotView.model_validate(
            {
                name: raw_metric[name]
                for name in MetricSnapshotView.model_fields
                if name in raw_metric
            }
        ).model_dump(mode="python")
        metric_name = str(metric["metric_name"])
        return {
            **metric,
            "label": _CUSTOMER_METRIC_LABELS_V2.get(metric_name, metric_name),
            "business_view": business_view,
            "exposure_role": exposure_role,
            "aggregation_method": aggregation_method,
            "definition": {
                "business_question": definition["business_question"],
                "denominator_description": definition["denominator_description"],
                "outcome_source": definition["outcome_source"],
                "query_predicate": definition["query_predicate"],
                "outcome_expression": definition["outcome_expression"],
                "required_semantic_capabilities": definition["required_semantic_capabilities"],
                "decision_task_refs": definition["decision_task_refs"],
                "semantic_rubric_ref": definition.get("semantic_rubric_ref"),
            },
        }

    def dashboard(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        business_view: str,
        exposure_role: str,
        metric_names: Sequence[str],
        start: date | None,
        end: date | None,
        models: Sequence[str] = (),
        regions: Sequence[str] = (),
        modes: Sequence[str] = (),
        focal_entity_id: str | None = None,
        publication_channel: str = "official",
    ) -> dict[str, Any]:
        if publication_channel not in {"official", "shadow"}:
            raise ValueError("invalid_customer_metric_publication_channel_v2")
        if (start is None) != (end is None) or (
            start is not None and end is not None and (start > end or (end - start).days > 366)
        ):
            raise ValueError("invalid_customer_metric_window_v2")
        if focal_entity_id is not None and (
            not focal_entity_id.strip() or len(focal_entity_id) > 200
        ):
            raise ValueError("invalid_customer_metric_focal_entity_v2")
        requested_names = self._validate_metric_names(
            business_view=business_view,
            exposure_role=exposure_role,
            metric_names=metric_names,
        )
        snapshot_set = self.repository.current_snapshot_set(
            tenant_pub_id=tenant_pub_id,
            project_pub_id=project_pub_id,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            models=self._filter_values(models, maximum_length=120),
            regions=self._filter_values(regions, maximum_length=120),
            modes=self._filter_values(modes, maximum_length=80),
            focal_entity_ids=(focal_entity_id,) if focal_entity_id else (),
            publication_channel=publication_channel,
        )
        if snapshot_set.get("project_pub_id") != project_pub_id:
            raise LookupError("metrics_v2_snapshot_set_not_found")
        focal_entities = [str(value) for value in snapshot_set.get("focal_entity_ids", ())]
        selected_focal = focal_entity_id
        if selected_focal is None:
            if len(focal_entities) != 1:
                raise ValueError("customer_dashboard_v2_requires_focal_entity_id")
            selected_focal = focal_entities[0]
        elif selected_focal not in focal_entities:
            raise LookupError("metrics_v2_snapshot_set_not_found")

        catalog = self._catalog()
        snapshots = {
            (str(metric["metric_name"]), str(metric["focal_entity_id"])): metric
            for metric in snapshot_set.get("metrics", ())
        }
        cards: list[dict[str, Any]] = []
        for metric_name in requested_names:
            raw_metric = snapshots.get((metric_name, selected_focal))
            if raw_metric is None:
                raise LookupError("customer_metric_snapshot_not_found_v2")
            definition = catalog.get(
                (
                    metric_name,
                    str(raw_metric["metric_version"]),
                    str(raw_metric["metric_definition_hash"]),
                )
            )
            if definition is None:
                raise LookupError("customer_metric_definition_not_found_v2")
            cards.append(
                self._metric_card(
                    raw_metric=raw_metric,
                    definition=definition,
                    business_view=business_view,
                    exposure_role=exposure_role,
                    aggregation_method=str(snapshot_set["aggregation_method"]),
                )
            )

        document = {
            "schema_version": "customer-dashboard-v2",
            "project_pub_id": project_pub_id,
            "brand_name": self._brand_name(
                tenant_pub_id=tenant_pub_id, project_pub_id=project_pub_id
            ),
            "business_view": business_view,
            "exposure_role": exposure_role,
            "publication_channel": publication_channel,
            "requested_metric_names": list(requested_names),
            "focal_entity_id": selected_focal,
            "snapshot_set_pub_id": snapshot_set["snapshot_set_pub_id"],
            "snapshot_set_hash": snapshot_set["snapshot_set_hash"],
            "state": snapshot_set["state"],
            "as_of": snapshot_set["as_of"],
            "window": snapshot_set["window"],
            "filters": snapshot_set["filters"],
            "aggregation_method": snapshot_set["aggregation_method"],
            "design_basis": snapshot_set["design_basis"],
            "scope_hash": snapshot_set["scope_hash"],
            "dependency_bundle_hash": snapshot_set["dependency_bundle_hash"],
            "metrics": cards,
        }
        return CustomerDashboardV2View.model_validate(document).model_dump(mode="python")

    def trace(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        snapshot_set_pub_id: str,
        expected_snapshot_set_hash: str,
        snapshot_pub_id: str,
        business_view: str,
        exposure_role: str,
        cursor: str | None,
        limit: int,
        eligibility_status: str | None = None,
        reason_code: str | None = None,
        query: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        hit: bool | None = None,
    ) -> dict[str, Any]:
        snapshot_set = self.repository.get_snapshot_set(
            tenant_pub_id=tenant_pub_id, set_pub_id=snapshot_set_pub_id
        )
        if (
            snapshot_set.get("project_pub_id") != project_pub_id
            or snapshot_set.get("snapshot_set_hash") != expected_snapshot_set_hash
        ):
            raise LookupError("metrics_v2_snapshot_set_not_found")
        raw_metric = self.repository.get_snapshot(
            tenant_pub_id=tenant_pub_id, snapshot_pub_id=snapshot_pub_id
        )
        if raw_metric.get("snapshot_set_pub_id") != snapshot_set_pub_id:
            raise LookupError("metrics_v2_snapshot_not_found")
        metric_name = str(raw_metric["metric_name"])
        self._validate_metric_names(
            business_view=business_view,
            exposure_role=exposure_role,
            metric_names=(metric_name,),
        )
        definition = self._catalog().get(
            (
                metric_name,
                str(raw_metric["metric_version"]),
                str(raw_metric["metric_definition_hash"]),
            )
        )
        if definition is None:
            raise LookupError("customer_metric_definition_not_found_v2")
        contributions = self.repository.list_contributions(
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
        binding_query = urlencode(
            {
                "metric_snapshot_set_pub_id": snapshot_set_pub_id,
                "metric_snapshot_set_hash": expected_snapshot_set_hash,
                "snapshot_at": str(snapshot_set["as_of"]),
                "start": str(snapshot_set["window"]["start"]),
                "end": str(snapshot_set["window"]["end"]),
            }
        )
        for row in contributions.get("data", ()):
            answer_href = str(row["answer_detail_href"])
            separator = "&" if "?" in answer_href else "?"
            row["answer_detail_href"] = f"{answer_href}{separator}{binding_query}"
        document = {
            "schema_version": "customer-metric-trace-v2",
            "project_pub_id": project_pub_id,
            "snapshot_set_pub_id": snapshot_set_pub_id,
            "snapshot_set_hash": expected_snapshot_set_hash,
            "as_of": snapshot_set["as_of"],
            "metric": self._metric_card(
                raw_metric=raw_metric,
                definition=definition,
                business_view=business_view,
                exposure_role=exposure_role,
                aggregation_method=str(snapshot_set["aggregation_method"]),
            ),
            "contributions": contributions,
        }
        return CustomerMetricTraceV2View.model_validate(document).model_dump(mode="python")


class CustomerDashboardService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def dashboard(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        generated_at: datetime | None = None,
    ) -> dict[str, Any]:
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = connection.execute(
                """
                SELECT p.id,p.name,
                       b.name AS brand_name,b.website AS brand_website,
                       acv.website AS confirmed_website
                FROM platform.project p
                LEFT JOIN LATERAL (
                  SELECT name,website FROM platform.brand
                  WHERE project_id=p.id
                  ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                ) b ON true
                LEFT JOIN LATERAL (
                  SELECT website FROM platform.asset_confirmation_version
                  WHERE project_id=p.id
                  ORDER BY revision DESC,created_at DESC,pub_id DESC LIMIT 1
                ) acv ON true
                WHERE p.pub_id=%s
                """,
                (project_pub_id,),
            ).fetchone()
            if project is None:
                raise LookupError("project_not_found")

            brand_name = str(project["brand_name"] or project["name"]).strip() or "未命名品牌"
            official_host = own_site_host(project["confirmed_website"] or project["brand_website"])
            competitors = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM platform.competitor
                    WHERE project_id=%s
                    ORDER BY updated_at DESC,pub_id
                    """,
                    (project["id"],),
                ).fetchall()
                if str(row["name"]).strip()
            ]
            query_rows = connection.execute(
                """
                SELECT qi.pub_id,qi.text,qg.name AS group_name
                FROM platform.query_item qi
                JOIN platform.query_group qg ON qg.id=qi.group_id
                WHERE qg.project_id=%s
                ORDER BY qg.name,qi.priority,qi.pub_id
                """,
                (project["id"],),
            ).fetchall()
            query_lookup = {
                str(row["pub_id"]): (str(row["text"]), str(row["group_name"])) for row in query_rows
            }

            answer_rows = connection.execute(
                """
                SELECT a.pub_id,a.capture_time,a.model,a.region,a.mode,
                       a.query_pub_id,a.query_text,a.response_text,
                       aa.analysis_run_pub_id,aa.mentioned,aa.rank,aa.sentiment,
                       aa.recommended,aa.competitor_ranks
                FROM analytics.answer a
                JOIN LATERAL (
                  SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommended,competitor_ranks
                  FROM analytics.answer_analysis
                  WHERE tenant_pub_id=a.tenant_pub_id AND answer_pub_id=a.pub_id
                  ORDER BY created_at DESC,id DESC LIMIT 1
                ) aa ON true
                WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                  AND a.capture_time::date BETWEEN %s AND %s
                  AND a.eligible
                  AND (%s::text IS NULL OR a.model=%s::text)
                  AND (%s::text IS NULL OR a.region=%s::text)
                  AND (%s::text IS NULL OR a.mode=%s::text)
                ORDER BY a.capture_time,a.pub_id
                """,
                (
                    tenant_pub_id,
                    project_pub_id,
                    start,
                    end,
                    model,
                    model,
                    region,
                    region,
                    mode,
                    mode,
                ),
            ).fetchall()
            answer_ids = [str(row["pub_id"]) for row in answer_rows]
            latest_analysis = {
                str(row["pub_id"]): str(row["analysis_run_pub_id"]) for row in answer_rows
            }
            citation_rows = (
                connection.execute(
                    """
                    SELECT answer_pub_id,analysis_run_pub_id,ordinal,canonical_url,host,
                           title,cited_text,own_source
                    FROM analytics.citation_fact
                    WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s::text[])
                    ORDER BY answer_pub_id,ordinal,id
                    """,
                    (tenant_pub_id, answer_ids),
                ).fetchall()
                if answer_ids
                else []
            )
            citations: dict[str, list[CustomerCitationFact]] = defaultdict(list)
            for row in citation_rows:
                answer_pub_id = str(row["answer_pub_id"])
                if str(row["analysis_run_pub_id"]) != latest_analysis.get(answer_pub_id):
                    continue
                host = str(row["host"] or "").strip().lower()
                if not host:
                    continue
                citations[answer_pub_id].append(
                    CustomerCitationFact(
                        canonical_url=str(row["canonical_url"]),
                        host=host,
                        own_source=bool(row["own_source"]) or is_own_site(host, official_host),
                        cited_text=row["cited_text"],
                        title=row["title"],
                    )
                )

            document_rows = connection.execute(
                """
                SELECT id,host FROM platform.source_document
                WHERE project_id=%s AND fetched_at::date BETWEEN %s AND %s
                ORDER BY fetched_at DESC,pub_id
                """,
                (project["id"], start, end),
            ).fetchall()
            document_hosts = {row["id"]: str(row["host"] or "").lower() for row in document_rows}
            audit_rows = (
                connection.execute(
                    """
                    SELECT DISTINCT ON (source_document_id,dimension)
                           source_document_id,dimension,verdict,audit_status
                    FROM platform.source_audit
                    WHERE source_document_id=ANY(%s::uuid[])
                    ORDER BY source_document_id,dimension,prompt_version DESC,pub_id DESC
                    """,
                    ([row["id"] for row in document_rows],),
                ).fetchall()
                if document_rows
                else []
            )
            source_audits = tuple(
                CustomerSourceAuditFact(
                    host=document_hosts.get(row["source_document_id"], ""),
                    dimension=str(row["dimension"]),
                    verdict=str(row["verdict"]) if row["verdict"] is not None else None,
                    audit_status=str(row["audit_status"]),
                    own_source=is_own_site(
                        document_hosts.get(row["source_document_id"], ""), official_host
                    ),
                )
                for row in audit_rows
            )

            risk_rows = connection.execute(
                """
                SELECT platform,subject_brand,target_brand,attitude,disparagement,
                       confidence,created_at
                FROM platform.disparagement_judgment
                WHERE project_id=%s AND created_at::date BETWEEN %s AND %s
                  AND judgment_status='ok' AND disparagement IS NOT NULL
                ORDER BY created_at,pub_id
                """,
                (project["id"], start, end),
            ).fetchall()

        answer_facts: list[CustomerAnswerFact] = []
        for row in answer_rows:
            query_pub_id = str(row["query_pub_id"]) if row["query_pub_id"] else None
            query_definition = query_lookup.get(query_pub_id or "")
            response_text = str(row["response_text"])
            query_text = (
                query_definition[0]
                if query_definition
                else (str(row["query_text"]) if row["query_text"] else None)
            )
            rank = int(row["rank"]) if row["rank"] is not None else None
            mentioned = bool(row["mentioned"])
            recommended = (
                bool(row["recommended"])
                if row["recommended"] is not None
                else infer_recommendation(
                    query_text=query_text,
                    response_text=response_text,
                    brand_name=brand_name,
                    mentioned=mentioned,
                    rank=rank,
                )
            )
            answer_facts.append(
                CustomerAnswerFact(
                    answer_pub_id=str(row["pub_id"]),
                    capture_time=row["capture_time"],
                    model=str(row["model"]),
                    region=str(row["region"]),
                    mode=str(row["mode"]),
                    query_pub_id=query_pub_id,
                    query_text=query_text,
                    query_group=query_definition[1] if query_definition else None,
                    response_text=response_text,
                    mentioned=mentioned,
                    rank=rank,
                    sentiment=str(row["sentiment"]) if row["sentiment"] is not None else None,
                    recommended=recommended,
                    competitor_mentions=infer_competitor_mentions(response_text, competitors),
                    competitor_ranks=_safe_competitor_ranks(row["competitor_ranks"]),
                    citations=tuple(citations.get(str(row["pub_id"]), ())),
                )
            )
        risk_facts = tuple(
            CustomerRiskFact(
                model=str(row["platform"] or "未知平台"),
                subject_brand=str(row["subject_brand"] or ""),
                target_brand=str(row["target_brand"]),
                attitude=str(row["attitude"] or "neutral"),
                disparagement=bool(row["disparagement"]),
                confidence=(
                    Decimal(str(row["confidence"])) if row["confidence"] is not None else None
                ),
                created_at=row["created_at"],
            )
            for row in risk_rows
            if str(row["target_brand"]).casefold() == brand_name.casefold()
        )
        filters = {
            key: value
            for key, value in {"model": model, "region": region, "mode": mode}.items()
            if value is not None
        }
        bundle = build_customer_metric_bundle(
            project_pub_id=project_pub_id,
            brand_name=brand_name,
            competitor_names=competitors,
            answers=answer_facts,
            source_audits=source_audits,
            risks=risk_facts,
            generated_at=generated_at or datetime.now(UTC),
            window_start=start,
            window_end=end,
            filters=filters,
        )
        assert_customer_projection_safe(bundle)
        return bundle

    def answer_page(
        self,
        *,
        tenant_pub_id: str,
        project_pub_id: str,
        start: date,
        end: date,
        search: str | None = None,
        model: str | None = None,
        region: str | None = None,
        mode: str | None = None,
        mentioned: bool | None = None,
        sentiment: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return customer facts from eligible answers and their latest analysis."""

        normalized_search = search.strip() if search and search.strip() else None
        with _customer_connection(self.dsn, tenant_pub_id) as connection:
            project = connection.execute(
                """
                SELECT p.id,p.name,b.name AS brand_name
                FROM platform.project p
                LEFT JOIN LATERAL (
                  SELECT name FROM platform.brand
                  WHERE project_id=p.id
                  ORDER BY updated_at DESC,pub_id DESC LIMIT 1
                ) b ON true
                WHERE p.pub_id=%s
                """,
                (project_pub_id,),
            ).fetchone()
            if project is None:
                raise LookupError("project_not_found")
            brand_name = str(project["brand_name"] or project["name"]).strip() or "未命名品牌"

            common_parameters = (
                project["id"],
                tenant_pub_id,
                project_pub_id,
                start,
                end,
                model,
                model,
                region,
                region,
                mode,
                mode,
                mentioned,
                mentioned,
                sentiment,
                sentiment,
                normalized_search,
                normalized_search,
                normalized_search,
            )
            answer_relation = """
                WITH query_definition AS (
                  SELECT qi.pub_id,qi.text
                  FROM platform.query_item qi
                  JOIN platform.query_group qg ON qg.id=qi.group_id
                  WHERE qg.project_id=%s
                ), customer_answer AS (
                  SELECT a.pub_id,a.query_pub_id,
                         COALESCE(NULLIF(BTRIM(a.query_text),''),qd.text) AS query_text,
                         a.response_text,a.response_raw,a.model,a.region,a.mode,a.capture_time,
                         aa.analysis_run_pub_id,aa.mentioned,aa.rank,aa.sentiment,aa.recommended
                  FROM analytics.answer a
                  JOIN LATERAL (
                    SELECT analysis_run_pub_id,mentioned,rank,sentiment,recommended
                    FROM analytics.answer_analysis
                    WHERE tenant_pub_id=a.tenant_pub_id AND answer_pub_id=a.pub_id
                    ORDER BY created_at DESC,id DESC LIMIT 1
                  ) aa ON true
                  LEFT JOIN query_definition qd ON qd.pub_id=a.query_pub_id
                  WHERE a.tenant_pub_id=%s AND a.project_pub_id=%s
                    AND a.capture_time::date BETWEEN %s AND %s
                    AND a.eligible
                    AND (%s::text IS NULL OR a.model=%s::text)
                    AND (%s::text IS NULL OR a.region=%s::text)
                    AND (%s::text IS NULL OR a.mode=%s::text)
                    AND (%s::boolean IS NULL OR aa.mentioned=%s::boolean)
                    AND (%s::text IS NULL OR aa.sentiment=%s::text)
                    AND (
                      %s::text IS NULL
                      OR STRPOS(LOWER(COALESCE(NULLIF(BTRIM(a.query_text),''),qd.text,'')),
                                LOWER(%s::text)) > 0
                      OR STRPOS(LOWER(COALESCE(a.response_plain_text,a.response_text)),
                                LOWER(%s::text)) > 0
                    )
                )
            """
            count_row = connection.execute(
                answer_relation + "SELECT count(*) AS total FROM customer_answer",
                common_parameters,
            ).fetchone()
            total = int(count_row["total"]) if count_row is not None else 0
            rows = connection.execute(
                answer_relation
                + """
                SELECT ca.*,
                       (SELECT count(*)
                        FROM analytics.citation_fact cf
                        WHERE cf.tenant_pub_id=%s
                          AND cf.answer_pub_id=ca.pub_id
                          AND cf.analysis_run_pub_id=ca.analysis_run_pub_id) AS citation_count
                FROM customer_answer ca
                ORDER BY ca.capture_time DESC,ca.pub_id DESC
                OFFSET %s LIMIT %s
                """,
                common_parameters + (tenant_pub_id, offset, limit),
            ).fetchall()
            analysis_by_answer = {
                str(row["pub_id"]): str(row["analysis_run_pub_id"]) for row in rows
            }
            citation_rows = (
                connection.execute(
                    """
                    SELECT answer_pub_id,analysis_run_pub_id,ordinal,platform_ordinal,ordinal_base
                    FROM analytics.citation_fact
                    WHERE tenant_pub_id=%s AND answer_pub_id=ANY(%s::text[])
                    ORDER BY answer_pub_id,ordinal
                    """,
                    (tenant_pub_id, list(analysis_by_answer)),
                ).fetchall()
                if analysis_by_answer
                else []
            )

        citations_by_answer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for citation in citation_rows:
            answer_id = str(citation["answer_pub_id"])
            if str(citation["analysis_run_pub_id"]) != analysis_by_answer.get(answer_id):
                continue
            citations_by_answer[answer_id].append(
                {
                    "ordinal": int(citation["ordinal"]),
                    "platform_ordinal": int(citation["platform_ordinal"]),
                    "ordinal_base": int(citation["ordinal_base"]),
                }
            )
        data: list[dict[str, Any]] = []
        for row in rows:
            query_text = str(row["query_text"]) if row["query_text"] is not None else None
            raw_response = str(row.get("response_raw") or row["response_text"] or "")
            response_text = project_answer_content(
                raw_response, citations_by_answer.get(str(row["pub_id"]), [])
            ).response_markdown_normalized
            rank = int(row["rank"]) if row["rank"] is not None else None
            mentioned_value = bool(row["mentioned"])
            recommended = (
                bool(row["recommended"])
                if row["recommended"] is not None
                else infer_recommendation(
                    query_text=query_text,
                    response_text=response_text,
                    brand_name=brand_name,
                    mentioned=mentioned_value,
                    rank=rank,
                )
            )
            data.append(
                {
                    "answer_pub_id": str(row["pub_id"]),
                    "query_pub_id": (
                        str(row["query_pub_id"]) if row["query_pub_id"] is not None else None
                    ),
                    "query_text": query_text,
                    "response_text": response_text,
                    "model": str(row["model"]),
                    "region": str(row["region"]),
                    "mode": str(row["mode"]),
                    "capture_time": row["capture_time"],
                    "mentioned": mentioned_value,
                    "rank": rank,
                    "sentiment": (str(row["sentiment"]) if row["sentiment"] is not None else None),
                    "recommended": recommended,
                    "citation_count": int(row["citation_count"] or 0),
                }
            )
        document = {
            "schema_version": "customer-answer-page-v1",
            "project_pub_id": project_pub_id,
            "data": data,
            "page": {
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(data) < total,
            },
        }
        assert_customer_projection_safe(document)
        return document
