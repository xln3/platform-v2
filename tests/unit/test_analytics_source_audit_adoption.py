"""W2 官网内容采纳率三键 + 官网优化建议端点（契约表 T2）单元测试。

fake psycopg 连接按 SQL 片段派发脚本化结果，绝不打真 DB。覆盖：
- own_site_transcript_total/accurate/adoption_rate 只统计 own_site 文档
  （www/裸域/子域互配），第三方 host 绝不混入分子分母；
- audit_status != 'ok' 的判定不计入；分母为零时比率为 None（数据不足）；
- 官网 host 未知（无 asset_confirmation_version）时三键全零/None；
- site_audit_suggestions：T2 未迁移上线 → 全 None + 空数组（绝不 500）、
  未知项目同口径、正常路径取最新批次并原样带出建议行。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.analytics import service as service_module
from geo_platform.analytics.service import AnalyticsService

_TENANT = "tnt_0123456789abcdef"
_PROJECT = "prj_0123456789abcdef"
_START = datetime(2026, 8, 1, tzinfo=UTC).date()
_END = datetime(2026, 8, 10, tzinfo=UTC).date()


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


def _doc(
    doc_id: str,
    host: str,
    *,
    pub_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": doc_id,
        "pub_id": pub_id or f"srd_{doc_id}",
        "url": f"https://{host}/page-{doc_id}",
        "host": host,
        "final_url": None,
        "http_status": 200,
        "extract_status": "ok",
        "fetched_at": datetime(2026, 8, 5, tzinfo=UTC),
    }


def _audit(
    doc_id: str,
    *,
    dimension: str = "transcript",
    verdict: str | None = "accurate",
    audit_status: str = "ok",
) -> dict[str, Any]:
    return {
        "source_document_id": doc_id,
        "dimension": dimension,
        "verdict": verdict,
        "audit_status": audit_status,
        "rationale": None,
    }


class _OverviewFakeConnection:
    """source_audit_overview 的四条查询按 FROM 片段派发。"""

    def __init__(
        self,
        *,
        website: str | None,
        documents: list[dict[str, Any]],
        audits: list[dict[str, Any]],
        project_exists: bool = True,
    ) -> None:
        self._website = website
        self._documents = documents
        self._audits = audits
        self._project_exists = project_exists
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        self.statements.append(" ".join(sql.split()))
        if "FROM platform.project" in sql:
            return _Result([{"id": "project-uuid"}] if self._project_exists else [])
        if "FROM platform.asset_confirmation_version" in sql:
            return _Result([{"website": self._website}] if self._website is not None else [])
        if "FROM platform.source_document d" in sql:
            return _Result(list(self._documents))
        if "FROM platform.source_audit a" in sql:
            return _Result(list(self._audits))
        raise AssertionError(f"unexpected SQL: {sql}")


def _overview(
    monkeypatch: pytest.MonkeyPatch,
    *,
    website: str | None,
    documents: list[dict[str, Any]],
    audits: list[dict[str, Any]],
    project_exists: bool = True,
) -> dict[str, Any]:
    connection = _OverviewFakeConnection(
        website=website, documents=documents, audits=audits, project_exists=project_exists
    )

    @contextmanager
    def fake_platform_connection(dsn: str, tenant_pub_id: str) -> Any:
        yield connection

    monkeypatch.setattr(service_module, "_platform_tenant_connection", fake_platform_connection)
    return AnalyticsService(dsn="postgresql://fake").source_audit_overview(
        tenant_pub_id=_TENANT,
        project_pub_id=_PROJECT,
        start=_START,
        end=_END,
    )


def test_adoption_rate_counts_only_own_site_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [
        _doc("own1", "www.webray.com.cn"),
        _doc("own2", "docs.webray.com.cn"),  # 官网子域同样算 own_site
        _doc("third1", "news.thirdparty.com"),
    ]
    audits = [
        _audit("own1", verdict="accurate"),
        _audit("own2", verdict="inaccurate"),
        # 第三方 host 的判定绝不混入 own_site 分子分母。
        _audit("third1", verdict="accurate"),
    ]
    overview = _overview(
        monkeypatch,
        website="https://www.webray.com.cn/",
        documents=documents,
        audits=audits,
    )
    assert overview["own_site_transcript_total"] == 2
    assert overview["own_site_transcript_accurate"] == 1
    assert overview["own_site_adoption_rate"] == 0.5


def test_adoption_rate_ignores_non_ok_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = [_doc("own1", "webray.com.cn")]
    audits = [
        _audit("own1", verdict="accurate", audit_status="validation_failure"),
        _audit("own1", verdict=None, audit_status="llm_error"),
        # factual 维度不属于 transcript 采纳口径。
        _audit("own1", dimension="factual", verdict="accurate"),
    ]
    overview = _overview(
        monkeypatch,
        website="webray.com.cn",
        documents=documents,
        audits=audits,
    )
    assert overview["own_site_transcript_total"] == 0
    assert overview["own_site_transcript_accurate"] == 0
    assert overview["own_site_adoption_rate"] is None


def test_adoption_rate_none_when_own_site_host_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [_doc("own1", "www.webray.com.cn")]
    audits = [_audit("own1", verdict="accurate")]
    overview = _overview(
        monkeypatch,
        website=None,  # 无 asset_confirmation_version → own_site 判定一律 False
        documents=documents,
        audits=audits,
    )
    assert overview["own_site_host"] is None
    assert overview["own_site_transcript_total"] == 0
    assert overview["own_site_adoption_rate"] is None


def test_adoption_keys_zero_for_unknown_project(monkeypatch: pytest.MonkeyPatch) -> None:
    overview = _overview(
        monkeypatch, website=None, documents=[], audits=[], project_exists=False
    )
    assert overview["own_site_transcript_total"] == 0
    assert overview["own_site_transcript_accurate"] == 0
    assert overview["own_site_adoption_rate"] is None


# ---------------------------------------------------------------------------
# site_audit_suggestions（契约表 T2，降级优先）
# ---------------------------------------------------------------------------


class _SuggestionsFakeConnection:
    def __init__(
        self,
        *,
        table_exists: bool,
        project_exists: bool = True,
        latest: dict[str, Any] | None = None,
        suggestions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._table_exists = table_exists
        self._project_exists = project_exists
        self._latest = latest
        self._suggestions = suggestions or []
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        self.statements.append(" ".join(sql.split()))
        if "to_regclass" in sql:
            reg = "platform.site_audit_suggestion" if self._table_exists else None
            return _Result([{"reg": reg}])
        if "FROM platform.project" in sql:
            return _Result([{"id": "project-uuid"}] if self._project_exists else [])
        if "FROM platform.site_audit_suggestion s" in sql and "LIMIT 1" in sql:
            return _Result([self._latest] if self._latest is not None else [])
        if "FROM platform.site_audit_suggestion s" in sql:
            return _Result(list(self._suggestions))
        raise AssertionError(f"unexpected SQL: {sql}")


def _suggestions(
    monkeypatch: pytest.MonkeyPatch,
    connection: _SuggestionsFakeConnection,
) -> dict[str, Any]:
    @contextmanager
    def fake_platform_connection(dsn: str, tenant_pub_id: str) -> Any:
        yield connection

    monkeypatch.setattr(service_module, "_platform_tenant_connection", fake_platform_connection)
    return AnalyticsService(dsn="postgresql://fake").site_audit_suggestions(
        tenant_pub_id=_TENANT,
        project_pub_id=_PROJECT,
    )


def test_suggestions_degrade_when_table_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _SuggestionsFakeConnection(table_exists=False)
    result = _suggestions(monkeypatch, connection)
    assert result == {
        "batch_pub_id": None,
        "generated_at": None,
        "model": None,
        "suggestions": [],
    }
    # 只探测表存在性，绝不触碰未迁移的契约表。
    assert len(connection.statements) == 1


def test_suggestions_empty_for_unknown_project(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _SuggestionsFakeConnection(table_exists=True, project_exists=False)
    result = _suggestions(monkeypatch, connection)
    assert result["batch_pub_id"] is None
    assert result["suggestions"] == []


def test_suggestions_empty_when_no_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _SuggestionsFakeConnection(table_exists=True, latest=None)
    result = _suggestions(monkeypatch, connection)
    assert result["batch_pub_id"] is None
    assert result["suggestions"] == []


def test_suggestions_return_latest_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    connection = _SuggestionsFakeConnection(
        table_exists=True,
        latest={"batch_pub_id": "sasb_1", "model": "gpt-5.6-luna", "created_at": created_at},
        suggestions=[
            {
                "category": "citability",
                "severity": "high",
                "title": "产品页缺少规格表",
                "detail": "补充结构化规格表。",
                "evidence_document_pub_id": "srd_own1",
            },
            {
                "category": "crawlability",
                "severity": "low",
                "title": "未提交 sitemap",
                "detail": "提交 sitemap。",
                "evidence_document_pub_id": None,
            },
        ],
    )
    result = _suggestions(monkeypatch, connection)
    assert result["batch_pub_id"] == "sasb_1"
    assert result["generated_at"] == created_at
    assert result["model"] == "gpt-5.6-luna"
    assert [row["category"] for row in result["suggestions"]] == [
        "citability",
        "crawlability",
    ]
    assert result["suggestions"][0]["evidence_document_pub_id"] == "srd_own1"
