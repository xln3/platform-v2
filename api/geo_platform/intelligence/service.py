from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from psycopg.rows import dict_row

from domain.evidence.diff import compare_evidence
from domain.intelligence.core import (
    DetectionResult,
    EvidenceRelation,
    SourceAssessment,
    extract_claims,
    score_investigation,
)
from geo_platform.tenancy.ids import new_pub_id
from geo_platform.tenancy.psycopg import tenant_connection


class IntelligenceService:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def create_investigation(
        self, *, tenant_pub_id: str, title: str, access_class: str = "customer_private"
    ) -> str:
        investigation_pub_id = new_pub_id("inv")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.investigation
                  (pub_id,tenant_pub_id,title,state,access_class)
                VALUES (%s,%s,%s,'collecting',%s)
                """,
                (investigation_pub_id, tenant_pub_id, title, access_class),
            )
        return investigation_pub_id

    def register_source(
        self,
        *,
        tenant_pub_id: str,
        platform: str,
        opaque_author_id: str,
        display_name: str | None,
        host: str,
        ownership_cluster: str | None,
        authority_class: str | None,
        observed_at: datetime,
    ) -> tuple[str, str]:
        author_pub_id = new_pub_id("authr")
        domain_pub_id = new_pub_id("dom")
        display_name_hash = sha256(display_name.encode()).hexdigest() if display_name else None
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            author = connection.execute(
                """
                INSERT INTO intelligence.author_identity
                  (pub_id,tenant_pub_id,platform,opaque_author_id,display_name_hash,
                   first_seen_at,last_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,platform,opaque_author_id)
                DO UPDATE SET last_seen_at=GREATEST(
                  intelligence.author_identity.last_seen_at,EXCLUDED.last_seen_at)
                RETURNING pub_id
                """,
                (
                    author_pub_id,
                    tenant_pub_id,
                    platform,
                    opaque_author_id,
                    display_name_hash,
                    observed_at,
                    observed_at,
                ),
            ).fetchone()
            domain = connection.execute(
                """
                INSERT INTO intelligence.domain_profile
                  (pub_id,tenant_pub_id,host,ownership_cluster,authority_class,first_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,host)
                DO UPDATE SET
                  ownership_cluster=COALESCE(EXCLUDED.ownership_cluster,
                    intelligence.domain_profile.ownership_cluster),
                  authority_class=COALESCE(EXCLUDED.authority_class,
                    intelligence.domain_profile.authority_class)
                RETURNING pub_id
                """,
                (
                    domain_pub_id,
                    tenant_pub_id,
                    host.lower(),
                    ownership_cluster,
                    authority_class,
                    observed_at,
                ),
            ).fetchone()
            assert author is not None and domain is not None
        return author["pub_id"], domain["pub_id"]

    def register_entity(
        self,
        *,
        tenant_pub_id: str,
        entity_type: str,
        canonical_name: str,
        aliases: Sequence[str] = (),
    ) -> str:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            existing = connection.execute(
                """
                SELECT pub_id FROM intelligence.entity
                WHERE tenant_pub_id=%s AND entity_type=%s
                  AND lower(canonical_name)=lower(%s)
                ORDER BY id LIMIT 1
                """,
                (tenant_pub_id, entity_type, canonical_name),
            ).fetchone()
            if existing is not None:
                return str(existing["pub_id"])
            entity_pub_id = new_pub_id("ent")
            connection.execute(
                """
                INSERT INTO intelligence.entity
                  (pub_id,tenant_pub_id,entity_type,canonical_name,aliases)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (entity_pub_id, tenant_pub_id, entity_type, canonical_name, list(aliases)),
            )
        return entity_pub_id

    def link_graph(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        from_pub_id: str,
        to_pub_id: str,
        relation: str,
        weight: Decimal | None = None,
        evidence_pub_id: str | None = None,
    ) -> None:
        allowed = {
            "supports",
            "contradicts",
            "insufficient",
            "derived_from",
            "near_duplicate",
            "published_by",
            "cites",
            "mentions",
        }
        if relation not in allowed:
            raise ValueError("unsupported investigation graph relation")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.graph_edge
                  (tenant_pub_id,investigation_pub_id,from_pub_id,to_pub_id,relation,
                   weight,evidence_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,from_pub_id,to_pub_id,relation)
                DO UPDATE SET weight=EXCLUDED.weight,
                  evidence_pub_id=COALESCE(EXCLUDED.evidence_pub_id,
                    intelligence.graph_edge.evidence_pub_id)
                """,
                (
                    tenant_pub_id,
                    investigation_pub_id,
                    from_pub_id,
                    to_pub_id,
                    relation,
                    weight,
                    evidence_pub_id,
                ),
            )

    def record_similarity(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        left_content_version_pub_id: str,
        right_content_version_pub_id: str,
        body_hash_equal: bool,
        semantic_similarity: Decimal,
        same_source_cluster: bool,
    ) -> None:
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.similarity_edge
                  (tenant_pub_id,investigation_pub_id,left_content_version_pub_id,
                   right_content_version_pub_id,body_hash_equal,semantic_similarity,
                   same_source_cluster)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,left_content_version_pub_id,
                  right_content_version_pub_id)
                DO UPDATE SET body_hash_equal=EXCLUDED.body_hash_equal,
                  semantic_similarity=EXCLUDED.semantic_similarity,
                  same_source_cluster=EXCLUDED.same_source_cluster
                """,
                (
                    tenant_pub_id,
                    investigation_pub_id,
                    left_content_version_pub_id,
                    right_content_version_pub_id,
                    body_hash_equal,
                    semantic_similarity,
                    same_source_cluster,
                ),
            )
        if semantic_similarity >= Decimal("0.85") or body_hash_equal:
            self.link_graph(
                tenant_pub_id=tenant_pub_id,
                investigation_pub_id=investigation_pub_id,
                from_pub_id=left_content_version_pub_id,
                to_pub_id=right_content_version_pub_id,
                relation="near_duplicate",
                weight=semantic_similarity,
            )

    def ingest_content(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        canonical_url: str,
        title: str,
        body_text: str,
        embedding: Sequence[float],
        access_class: str,
        captured_at: datetime,
        published_at: datetime | None,
        evidence_pub_id: str | None,
        author_pub_id: str | None = None,
        domain_pub_id: str | None = None,
    ) -> dict[str, Any]:
        body_hash = sha256(body_text.encode()).hexdigest()
        content_pub_id = new_pub_id("cnt")
        version_pub_id = new_pub_id("cntv")
        previous_version: dict[str, Any] | None = None
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            existing_version = connection.execute(
                """
                SELECT cv.pub_id,cv.content_pub_id,cv.body_hash
                FROM intelligence.content_version cv
                WHERE cv.tenant_pub_id=%s AND cv.body_hash=%s
                """,
                (tenant_pub_id, body_hash),
            ).fetchone()
            if existing_version is not None:
                return {
                    "content_pub_id": existing_version["content_pub_id"],
                    "version_pub_id": existing_version["pub_id"],
                    "body_hash": body_hash,
                    "deduplicated": True,
                }
            existing_content = connection.execute(
                """
                SELECT pub_id FROM intelligence.content_item
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s AND canonical_url=%s
                """,
                (tenant_pub_id, investigation_pub_id, canonical_url),
            ).fetchone()
            if existing_content is None:
                connection.execute(
                    """
                    INSERT INTO intelligence.content_item
                      (pub_id,tenant_pub_id,investigation_pub_id,canonical_url,content_type,
                       author_pub_id,domain_pub_id,access_class)
                    VALUES (%s,%s,%s,%s,'post',%s,%s,%s)
                    """,
                    (
                        content_pub_id,
                        tenant_pub_id,
                        investigation_pub_id,
                        canonical_url,
                        author_pub_id,
                        domain_pub_id,
                        access_class,
                    ),
                )
                version_number = 1
            else:
                content_pub_id = existing_content["pub_id"]
                previous_version_row = connection.execute(
                    """
                    SELECT version_number,body_text,evidence_pub_id
                    FROM intelligence.content_version
                    WHERE tenant_pub_id=%s AND content_pub_id=%s
                    ORDER BY version_number DESC LIMIT 1
                    """,
                    (tenant_pub_id, content_pub_id),
                ).fetchone()
                assert previous_version_row is not None
                previous_version = dict(previous_version_row)
                version_number = previous_version["version_number"] + 1
            connection.execute(
                """
                INSERT INTO intelligence.content_version
                  (pub_id,tenant_pub_id,content_pub_id,version_number,body_hash,title,
                   body_text,embedding,embedding_vector,evidence_pub_id,published_at,captured_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s,%s)
                """,
                (
                    version_pub_id,
                    tenant_pub_id,
                    content_pub_id,
                    version_number,
                    body_hash,
                    title,
                    body_text,
                    list(embedding),
                    _vector_literal(embedding),
                    evidence_pub_id,
                    published_at,
                    captured_at,
                ),
            )
            if evidence_pub_id is not None:
                connection.execute(
                    """
                    INSERT INTO evidence.evidence_snapshot
                      (pub_id,tenant_pub_id,subject_pub_id,evidence_pub_id,snapshot_number,
                       normalized_text_hash,perceptual_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,NULL)
                    ON CONFLICT (tenant_pub_id,subject_pub_id,snapshot_number) DO NOTHING
                    """,
                    (
                        new_pub_id("snap"),
                        tenant_pub_id,
                        content_pub_id,
                        evidence_pub_id,
                        version_number,
                        body_hash,
                    ),
                )
                previous_evidence_pub_id = (
                    previous_version.get("evidence_pub_id") if previous_version else None
                )
                if (
                    isinstance(previous_evidence_pub_id, str)
                    and previous_evidence_pub_id != evidence_pub_id
                ):
                    assert previous_version is not None
                    compared = compare_evidence(
                        str(previous_version["body_text"]),
                        body_text,
                    )
                    connection.execute(
                        """
                        INSERT INTO evidence.evidence_diff
                          (pub_id,tenant_pub_id,before_evidence_pub_id,after_evidence_pub_id,
                           text_diff,similarity)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (tenant_pub_id,before_evidence_pub_id,after_evidence_pub_id)
                        DO NOTHING
                        """,
                        (
                            new_pub_id("diff"),
                            tenant_pub_id,
                            previous_evidence_pub_id,
                            evidence_pub_id,
                            json.dumps(
                                {
                                    "unified": compared.unified_text_diff,
                                    "before_hash": compared.before_hash,
                                    "after_hash": compared.after_hash,
                                    "visual_similarity": compared.visual_similarity,
                                }
                            ),
                            compared.text_similarity,
                        ),
                    )
            connection.execute(
                """
                INSERT INTO intelligence.propagation_event
                  (pub_id,tenant_pub_id,investigation_pub_id,content_version_pub_id,
                   source_cluster,observed_at,published_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    new_pub_id("prop"),
                    tenant_pub_id,
                    investigation_pub_id,
                    version_pub_id,
                    domain_pub_id or f"url:{canonical_url.split('/')[2].lower()}",
                    captured_at,
                    published_at,
                ),
            )
            claim_rows = []
            for claim in extract_claims(body_text):
                claim_pub_id = new_pub_id("clm")
                occurrence_pub_id = new_pub_id("occ")
                connection.execute(
                    """
                    INSERT INTO intelligence.claim
                      (pub_id,tenant_pub_id,investigation_pub_id,normalized_text,verifiability)
                    VALUES (%s,%s,%s,%s,'unreviewed')
                    """,
                    (
                        claim_pub_id,
                        tenant_pub_id,
                        investigation_pub_id,
                        claim.text,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO intelligence.claim_occurrence
                      (pub_id,tenant_pub_id,claim_pub_id,content_version_pub_id,text_start,
                       text_end,quote_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        occurrence_pub_id,
                        tenant_pub_id,
                        claim_pub_id,
                        version_pub_id,
                        claim.start,
                        claim.end,
                        claim.normalized_hash,
                    ),
                )
                claim_rows.append(
                    {
                        "claim_pub_id": claim_pub_id,
                        "text": claim.text,
                        "start": claim.start,
                        "end": claim.end,
                    }
                )
        return {
            "content_pub_id": content_pub_id,
            "version_pub_id": version_pub_id,
            "body_hash": body_hash,
            "deduplicated": False,
            "claims": claim_rows,
        }

    def hybrid_search(
        self,
        *,
        tenant_pub_id: str,
        query: str,
        query_embedding: Sequence[float],
        limit: int = 20,
        include_private: bool = False,
    ) -> list[dict[str, Any]]:
        access_clause = "" if include_private else "AND ci.access_class='public'"
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT cv.pub_id,cv.content_pub_id,cv.title,cv.body_text,cv.embedding,
                       cv.captured_at,ci.canonical_url,ci.access_class,
                       ts_rank_cd(cv.search_vector, plainto_tsquery('simple', %s)) AS text_rank,
                       1-(cv.embedding_vector <=> %s::vector) AS semantic_rank
                FROM intelligence.content_version cv
                JOIN intelligence.content_item ci ON ci.pub_id=cv.content_pub_id
                WHERE cv.tenant_pub_id=%s {access_clause}
                  AND (cv.search_vector @@ plainto_tsquery('simple', %s)
                       OR cv.embedding IS NOT NULL)
                ORDER BY text_rank DESC,cv.captured_at DESC
                LIMIT 200
                """,
                (query, _vector_literal(query_embedding), tenant_pub_id, query),
            ).fetchall()
        ranked = []
        for row in rows:
            semantic = float(row["semantic_rank"] or 0)
            hybrid = float(row["text_rank"]) * 0.45 + semantic * 0.55
            ranked.append({**row, "semantic_score": semantic, "hybrid_score": hybrid})
        return sorted(ranked, key=lambda row: row["hybrid_score"], reverse=True)[:limit]

    def add_claim_evidence(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        claim_pub_id: str,
        evidence_pub_id: str,
        relation: EvidenceRelation,
        source_cluster: str,
        independence_weight: Decimal,
        rationale: str,
        from_pub_id: str,
    ) -> str:
        link_pub_id = new_pub_id("ce")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.claim_evidence
                  (pub_id,tenant_pub_id,claim_pub_id,evidence_pub_id,relation,
                   source_cluster,independence_weight,rationale)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,claim_pub_id,evidence_pub_id) DO NOTHING
                """,
                (
                    link_pub_id,
                    tenant_pub_id,
                    claim_pub_id,
                    evidence_pub_id,
                    relation.value,
                    source_cluster,
                    independence_weight,
                    rationale,
                ),
            )
            connection.execute(
                """
                INSERT INTO intelligence.graph_edge
                  (tenant_pub_id,investigation_pub_id,from_pub_id,to_pub_id,relation,
                   weight,evidence_pub_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (
                    tenant_pub_id,
                    investigation_pub_id,
                    from_pub_id,
                    claim_pub_id,
                    relation.value,
                    independence_weight,
                    evidence_pub_id,
                ),
            )
        return link_pub_id

    def record_source_independence(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        source_pub_id: str,
        cluster_id: str,
        independence_weight: Decimal,
        circular_citation_risk: Decimal,
        reasons: Sequence[str],
        rule_version: str,
    ) -> str:
        assessment_pub_id = new_pub_id("srca")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.source_independence
                  (pub_id,tenant_pub_id,investigation_pub_id,source_pub_id,cluster_id,
                   independence_weight,circular_citation_risk,reasons,rule_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,investigation_pub_id,source_pub_id)
                DO UPDATE SET cluster_id=EXCLUDED.cluster_id,
                  independence_weight=EXCLUDED.independence_weight,
                  circular_citation_risk=EXCLUDED.circular_citation_risk,
                  reasons=EXCLUDED.reasons,rule_version=EXCLUDED.rule_version
                """,
                (
                    assessment_pub_id,
                    tenant_pub_id,
                    investigation_pub_id,
                    source_pub_id,
                    cluster_id,
                    independence_weight,
                    circular_citation_risk,
                    json.dumps(list(reasons), ensure_ascii=False),
                    rule_version,
                ),
            )
        return assessment_pub_id

    def record_feature(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        subject_pub_id: str,
        feature_family: str,
        feature_name: str,
        feature_value: Decimal,
        explanation: str,
        rule_version: str,
    ) -> str:
        if feature_family not in {"content", "source", "propagation", "external_fact"}:
            raise ValueError("unsupported feature family")
        feature_pub_id = new_pub_id("feat")
        event_id = new_pub_id("evt")
        event_time = datetime.now(UTC)
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            connection.execute(
                """
                INSERT INTO intelligence.detection_feature
                  (pub_id,tenant_pub_id,investigation_pub_id,subject_pub_id,feature_family,
                   feature_name,feature_value,explanation,rule_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,subject_pub_id,feature_name,rule_version)
                DO UPDATE SET feature_value=EXCLUDED.feature_value,
                  explanation=EXCLUDED.explanation
                """,
                (
                    feature_pub_id,
                    tenant_pub_id,
                    investigation_pub_id,
                    subject_pub_id,
                    feature_family,
                    feature_name,
                    feature_value,
                    explanation,
                    rule_version,
                ),
            )
            connection.execute(
                """
                INSERT INTO integration.outbox_event
                  (event_id,tenant_pub_id,event_type,aggregate_pub_id,trace_id,payload,
                   occurred_at)
                VALUES (%s,%s,'intelligence.feature.recorded',%s,%s,%s,%s)
                """,
                (
                    event_id,
                    tenant_pub_id,
                    feature_pub_id,
                    investigation_pub_id,
                    json.dumps(
                        {
                            "investigation_pub_id": investigation_pub_id,
                            "subject_pub_id": subject_pub_id,
                            "feature_family": feature_family,
                            "feature_name": feature_name,
                            "feature_value": str(feature_value),
                            "rule_version": rule_version,
                            "model_version": "rules-only-experimental-v1",
                            "event_time": event_time.isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                    event_time,
                ),
            )
        return feature_pub_id

    def score(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        content_feature_score: Decimal,
        propagation_feature_score: Decimal,
        circular_citation_risk: Decimal,
        workflow_operation_id: str | None = None,
    ) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT ce.evidence_pub_id,ce.source_cluster,ce.relation,
                       ce.independence_weight,ea.access_class
                FROM intelligence.claim_evidence ce
                JOIN intelligence.claim c ON c.pub_id=ce.claim_pub_id
                JOIN evidence.evidence_asset ea ON ea.pub_id=ce.evidence_pub_id
                WHERE ce.tenant_pub_id=%s AND c.investigation_pub_id=%s
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchall()
            assessments = tuple(
                SourceAssessment(
                    source_pub_id=row["evidence_pub_id"],
                    source_cluster=row["source_cluster"],
                    relation=EvidenceRelation(row["relation"]),
                    independence_weight=row["independence_weight"],
                    access_class=row["access_class"],
                )
                for row in rows
            )
            result = score_investigation(
                assessments=assessments,
                content_feature_score=content_feature_score,
                propagation_feature_score=propagation_feature_score,
                circular_citation_risk=circular_citation_risk,
            )
            score_pub_id = new_pub_id("score")
            persisted = connection.execute(
                """
                INSERT INTO intelligence.detection_score
                  (pub_id,tenant_pub_id,investigation_pub_id,probability,
                   evidence_sufficiency,independent_source_count,uncertainty,rule_version,
                   model_version,explanation,workflow_operation_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,workflow_operation_id)
                  WHERE workflow_operation_id IS NOT NULL
                DO UPDATE SET pub_id=intelligence.detection_score.pub_id
                RETURNING pub_id,probability,evidence_sufficiency,independent_source_count,
                          uncertainty,rule_version,model_version,explanation
                """,
                (
                    score_pub_id,
                    tenant_pub_id,
                    investigation_pub_id,
                    result.probability,
                    result.evidence_sufficiency,
                    result.independent_source_count,
                    result.uncertainty,
                    result.rule_version,
                    result.model_version,
                    json.dumps(list(result.explanation), ensure_ascii=False),
                    workflow_operation_id,
                ),
            ).fetchone()
            assert persisted is not None
            persisted_result = DetectionResult(
                probability=persisted["probability"],
                evidence_sufficiency=persisted["evidence_sufficiency"],
                independent_source_count=persisted["independent_source_count"],
                uncertainty=persisted["uncertainty"],
                rule_version=persisted["rule_version"],
                model_version=persisted["model_version"],
                explanation=tuple(persisted["explanation"]),
            )
            connection.execute(
                """
                UPDATE intelligence.investigation SET state='review',updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (investigation_pub_id, tenant_pub_id),
            )
        return {"score_pub_id": persisted["pub_id"], "result": persisted_result}

    def verdict(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        verdict: str,
        reviewer_pub_id: str,
        rationale: str,
        workflow_operation_id: str | None = None,
    ) -> str:
        verdict_pub_id = new_pub_id("vrd")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            persisted = connection.execute(
                """
                INSERT INTO intelligence.human_verdict
                  (pub_id,tenant_pub_id,investigation_pub_id,verdict,reviewer_pub_id,rationale,
                   workflow_operation_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_pub_id,workflow_operation_id)
                  WHERE workflow_operation_id IS NOT NULL
                DO UPDATE SET pub_id=intelligence.human_verdict.pub_id
                RETURNING pub_id,investigation_pub_id,verdict,reviewer_pub_id,rationale
                """,
                (
                    verdict_pub_id,
                    tenant_pub_id,
                    investigation_pub_id,
                    verdict,
                    reviewer_pub_id,
                    rationale,
                    workflow_operation_id,
                ),
            ).fetchone()
            assert persisted is not None
            if (
                persisted["investigation_pub_id"],
                persisted["verdict"],
                persisted["reviewer_pub_id"],
                persisted["rationale"],
            ) != (investigation_pub_id, verdict, reviewer_pub_id, rationale):
                raise ValueError("workflow verdict replay payload drifted")
            connection.execute(
                """
                UPDATE intelligence.investigation SET state='decided',updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (investigation_pub_id, tenant_pub_id),
            )
        return str(persisted["pub_id"])

    def appeal(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        submitted_by_pub_id: str,
        reason: str,
    ) -> str:
        appeal_pub_id = new_pub_id("apl")
        with tenant_connection(self.dsn, tenant_pub_id) as connection:
            decided = connection.execute(
                """
                SELECT 1 FROM intelligence.human_verdict
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchone()
            if decided is None:
                raise PermissionError("only a decided investigation can be appealed")
            connection.execute(
                """
                INSERT INTO intelligence.appeal
                  (pub_id,tenant_pub_id,investigation_pub_id,state,submitted_by_pub_id,reason)
                VALUES (%s,%s,%s,'open',%s,%s)
                """,
                (
                    appeal_pub_id,
                    tenant_pub_id,
                    investigation_pub_id,
                    submitted_by_pub_id,
                    reason,
                ),
            )
            connection.execute(
                """
                UPDATE intelligence.investigation SET state='appealed',updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (investigation_pub_id, tenant_pub_id),
            )
        return appeal_pub_id

    def resolve_appeal(
        self,
        *,
        tenant_pub_id: str,
        investigation_pub_id: str,
        appeal_pub_id: str,
        reviewer_pub_id: str,
        resolution: str,
        corrected_verdict: str | None = None,
        rationale: str | None = None,
    ) -> str | None:
        if not rationale or not rationale.strip():
            raise ValueError("appeal resolution requires a rationale")
        if corrected_verdict is None and resolution not in {"upheld", "rejected"}:
            raise ValueError("uncorrected appeal resolution must be upheld or rejected")
        if corrected_verdict is not None and resolution != "corrected":
            raise ValueError("corrected verdict requires corrected resolution")
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            appeal = connection.execute(
                """
                SELECT pub_id,submitted_by_pub_id FROM intelligence.appeal
                WHERE pub_id=%s AND tenant_pub_id=%s AND investigation_pub_id=%s
                  AND state IN ('open','reviewing')
                FOR UPDATE
                """,
                (appeal_pub_id, tenant_pub_id, investigation_pub_id),
            ).fetchone()
            if appeal is None:
                raise LookupError("open appeal not found")
            prior_verdict = connection.execute(
                """
                SELECT pub_id,reviewer_pub_id FROM intelligence.human_verdict
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchone()
            if prior_verdict is None:
                raise LookupError("verdict to review not found")
            if reviewer_pub_id in {
                appeal["submitted_by_pub_id"],
                prior_verdict["reviewer_pub_id"],
            }:
                raise PermissionError("appeal requires an independent reviewer")
            replacement_pub_id: str | None = None
            state = resolution
            investigation_state = "decided"
            if corrected_verdict is not None:
                replacement_pub_id = new_pub_id("vrd")
                connection.execute(
                    """
                    INSERT INTO intelligence.human_verdict
                      (pub_id,tenant_pub_id,investigation_pub_id,verdict,reviewer_pub_id,
                       rationale,supersedes_pub_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        replacement_pub_id,
                        tenant_pub_id,
                        investigation_pub_id,
                        corrected_verdict,
                        reviewer_pub_id,
                        rationale,
                        prior_verdict["pub_id"],
                    ),
                )
                state = "corrected"
                investigation_state = "corrected"
            connection.execute(
                """
                UPDATE intelligence.appeal
                SET state=%s,resolution=%s,resolution_rationale=%s,
                    resolved_by_pub_id=%s,resolved_at=now(),updated_at=now()
                WHERE pub_id=%s
                """,
                (state, resolution, rationale, reviewer_pub_id, appeal_pub_id),
            )
            connection.execute(
                """
                UPDATE intelligence.investigation SET state=%s,updated_at=now()
                WHERE pub_id=%s AND tenant_pub_id=%s
                """,
                (investigation_state, investigation_pub_id, tenant_pub_id),
            )
        return replacement_pub_id

    def public_conclusion(self, *, tenant_pub_id: str, investigation_pub_id: str) -> dict[str, Any]:
        with tenant_connection(self.dsn, tenant_pub_id, row_factory=dict_row) as connection:
            score = connection.execute(
                """
                SELECT probability,evidence_sufficiency,independent_source_count,uncertainty,
                       rule_version,model_version,explanation
                FROM intelligence.detection_score
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchone()
            verdict = connection.execute(
                """
                SELECT verdict,rationale,created_at
                FROM intelligence.human_verdict
                WHERE tenant_pub_id=%s AND investigation_pub_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchone()
            evidence = connection.execute(
                """
                SELECT ce.relation,ce.source_cluster,ce.rationale,ea.pub_id,ea.sha256
                FROM intelligence.claim_evidence ce
                JOIN intelligence.claim c ON c.pub_id=ce.claim_pub_id
                JOIN evidence.evidence_asset ea ON ea.pub_id=ce.evidence_pub_id
                WHERE ce.tenant_pub_id=%s AND c.investigation_pub_id=%s
                  AND ea.access_class='public'
                ORDER BY ce.id
                """,
                (tenant_pub_id, investigation_pub_id),
            ).fetchall()
        if score is None or verdict is None:
            raise LookupError("investigation has no completed score and human verdict")
        return {
            "investigation_pub_id": investigation_pub_id,
            "probability": score["probability"],
            "evidence_sufficiency": score["evidence_sufficiency"],
            "independent_source_count": score["independent_source_count"],
            "uncertainty": score["uncertainty"],
            "rule_version": score["rule_version"],
            "model_version": score["model_version"],
            "explanation": score["explanation"],
            "human_verdict": dict(verdict),
            "public_evidence": [dict(row) for row in evidence],
            "disclaimer": "概率性辅助结论，不代表确定识别品牌实施 GEO；以人工裁决为准。",
        }


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _vector_literal(values: Sequence[float]) -> str:
    if len(values) > 384:
        raise ValueError("embedding dimension exceeds 384")
    padded = [*map(float, values), *([0.0] * (384 - len(values)))]
    return "[" + ",".join(format(value, ".9g") for value in padded) + "]"
