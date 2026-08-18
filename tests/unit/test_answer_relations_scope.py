from contextlib import contextmanager
from datetime import UTC, datetime

import geo_platform.analytics.router as analytics_router
from geo_platform.identity.policy import Principal, Role


class _Result:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


def test_answer_relations_bind_customer_project_and_latest_analysis(monkeypatch) -> None:
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

    result = analytics_router.answer_relations(
        "ans_test",
        "prj_test",
        Principal(
            subject="customer-test",
            role=Role.CUSTOMER,
            tenant_pub_id="tnt_test",
            user_pub_id="usr_test",
        ),
    )

    assert result.answer_pub_id == "ans_test"
    assert [citation.ordinal for citation in result.answer_citations] == [1]
    assert [evidence.pub_id for evidence in result.evidence] == ["evd_test"]

    answer_sql, answer_params = next(
        (sql, params)
        for sql, params in calls
        if "SELECT pub_id,project_pub_id FROM analytics.answer" in sql
    )
    assert "project_pub_id=%s::text" in answer_sql
    assert answer_params == ("tnt_test", "ans_test", "prj_test", "prj_test")

    citation_sql = next(sql for sql, _ in calls if "FROM analytics.citation_fact" in sql)
    assert "analysis_run_pub_id=(" in citation_sql
    assert "FROM analytics.answer_analysis aa" in citation_sql
    assert "ORDER BY aa.created_at DESC,aa.id DESC" in citation_sql
