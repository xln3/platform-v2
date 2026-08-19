from contextlib import contextmanager
from datetime import UTC, datetime

import geo_platform.analytics.router as analytics_router
import pytest
from fastapi import Response
from geo_platform.identity.policy import Principal, Role


class _Result:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


@pytest.mark.parametrize(
    ("role", "expected_evidence"),
    ((Role.CUSTOMER, []), (Role.OPERATOR, ["evd_test"])),
)
def test_answer_relations_bind_customer_project_and_latest_analysis(
    monkeypatch, role: Role, expected_evidence: list[str]
) -> None:
    calls: list[tuple[str, object]] = []
    captured_at = datetime(2026, 8, 17, 7, 42, tzinfo=UTC)

    class _Connection:
        def execute(self, sql: str, params: object = None) -> _Result:
            calls.append((sql, params))
            if "SELECT pub_id,project_pub_id FROM analytics.answer" in sql:
                return _Result([{"pub_id": "ans_test", "project_pub_id": "prj_test"}])
            if "FROM analytics.citation_fact" in sql:
                return _Result(
                    [
                        {
                            "pub_id": "cit_test",
                            "ordinal": 1,
                            "platform_ordinal": 0,
                            "ordinal_base": 0,
                            "canonical_url": "https://example.com/source",
                            "host": "example.com",
                            "title": "Source title",
                            "cited_text": "Cited passage",
                            "own_source": False,
                            "content_hash": "a" * 64,
                            "source_document_pub_id": "srd_test",
                            "published_at_raw": "2026-08-01",
                            "published_at": datetime(2026, 8, 1, tzinfo=UTC),
                            "published_at_timezone": "unknown",
                            "published_at_precision": "date",
                            "published_at_source": "jsonld.datePublished",
                            "published_at_confidence": "structured_only",
                        }
                    ]
                )
            if "FROM evidence.answer_share_artifact" in sql:
                return _Result(
                    [
                        {
                            "platform": "deepseek",
                            "status": "available",
                            "share_url": "https://chat.deepseek.com/share/test",
                            "final_url": "https://chat.deepseek.com/share/test",
                            "allowlist_valid": True,
                            "availability_status": "reachable",
                            "http_status": 200,
                            "checked_at": captured_at,
                            "last_accessible_at": captured_at,
                            "embed_status": "blocked",
                            "embed_reason": "x_frame_options_restricts_embedding",
                            "share_image_pub_id": "evd_testshareimage1234",
                            "share_image_sha256": "b" * 64,
                            "share_image_mime_type": "image/png",
                            "share_image_byte_size": 123,
                            "share_image_width": 2250,
                            "share_image_height": 25200,
                            "share_image_capture_time": captured_at,
                        }
                    ]
                )
            if "FROM evidence.evidence_relation" in sql:
                return _Result(
                    [
                        {
                            "pub_id": "evd_test",
                            "relation_type": "official_share_image",
                            "kind": "share_image",
                            "access_class": "customer",
                            "sha256": "b" * 64,
                            "mime_type": "image/png",
                            "byte_size": 123,
                            "image_width": 2250,
                            "image_height": 25200,
                            "source_url": None,
                            "capture_time": captured_at,
                        }
                    ]
                )
            if "FROM evidence.evidence_anchor" in sql:
                return _Result()
            if "FROM evidence.evidence_diff" in sql:
                return _Result()
            raise AssertionError(sql)

    @contextmanager
    def fake_tenant_connection(*_args: object, **_kwargs: object):
        yield _Connection()

    monkeypatch.setattr(analytics_router, "tenant_connection", fake_tenant_connection)
    monkeypatch.setattr(analytics_router, "_dsn", lambda: "postgresql://unused")

    response = Response()
    result = analytics_router.answer_relations(
        answer_pub_id="ans_test",
        response=response,
        project_pub_id="prj_test",
        snapshot_at=captured_at,
        principal=Principal(
            subject="customer-test",
            role=role,
            tenant_pub_id="tnt_test",
            user_pub_id="usr_test",
        ),
    )

    assert result.answer_pub_id == "ans_test"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert [citation.ordinal for citation in result.answer_citations] == [1]
    assert [evidence.pub_id for evidence in result.evidence] == expected_evidence
    assert result.share_artifact is not None
    assert result.share_artifact.share_url == "https://chat.deepseek.com/share/test"
    assert result.share_artifact.embed_status == "blocked"
    assert result.share_image is not None
    assert result.share_image.pub_id == "evd_testshareimage1234"
    if role is Role.CUSTOMER:
        assert not any("SELECT ea.pub_id,er.relation_type" in sql for sql, _ in calls)
        assert not any("FROM evidence.evidence_anchor" in sql for sql, _ in calls)
        assert not any("FROM evidence.evidence_diff" in sql for sql, _ in calls)
    else:
        evidence_sql = next(sql for sql, _ in calls if "FROM evidence.evidence_relation" in sql)
        anchor_sql = next(sql for sql, _ in calls if "FROM evidence.evidence_anchor" in sql)
        history_sql = next(sql for sql, _ in calls if "FROM evidence.evidence_diff" in sql)
        assert "er.created_at<=%s::timestamptz" in evidence_sql
        assert "ea.created_at<=%s::timestamptz" in evidence_sql
        assert "created_at<=%s::timestamptz" in anchor_sql
        assert "created_at<=%s::timestamptz" in history_sql

    answer_sql, answer_params = next(
        (sql, params)
        for sql, params in calls
        if "SELECT pub_id,project_pub_id FROM analytics.answer" in sql
    )
    assert "project_pub_id=%s::text" in answer_sql
    assert answer_params == (
        "tnt_test",
        "ans_test",
        "prj_test",
        "prj_test",
        captured_at,
        captured_at,
    )

    citation_sql = next(sql for sql, _ in calls if "FROM analytics.citation_fact" in sql)
    assert "analysis_run_pub_id=(" in citation_sql
    assert "FROM analytics.answer_analysis aa" in citation_sql
    assert "c.created_at<=%s::timestamptz" in citation_sql
    assert "aa.created_at<=%s::timestamptz" in citation_sql
    assert "relation.updated_at<=%s::timestamptz" in citation_sql
    assert "ORDER BY aa.created_at DESC,aa.id DESC" in citation_sql
    share_sql = next(sql for sql, _ in calls if "FROM evidence.answer_share_artifact" in sql)
    assert "updated_at<=%s::timestamptz" in share_sql
    assert "relation.relation_type='official_share_image'" in share_sql
    assert "asset.kind='share_image'" in share_sql
    assert "asset.customer_visible=true" in share_sql
