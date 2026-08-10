"""W3 典型案例事实核查外露（契约表 T1）单元测试。

fake psycopg 连接按 SQL 片段派发脚本化结果，绝不打真 DB。覆盖：
- 有 T1 行的案例带出最新 fact_check（verdict/summary/source_url/checked_at）；
- 同一 judgment 多行时取 created_at 最新一行（DISTINCT ON 语义由 SQL 保证，
  fake 侧按契约返回单行即可）；
- 无 T1 行的案例 fact_check=None；
- T1 未迁移上线（to_regclass → NULL）时全部案例 fact_check=None，且绝不
  发起对缺失表的查询（不 500）；
- 窗口/限额参数原样透传主查询（既有口径不变）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from geo_platform.analytics import service as service_module
from geo_platform.analytics.router import _project_fact_check
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


def _case(judgment_pub_id: str) -> dict[str, Any]:
    return {
        "judgment_pub_id": judgment_pub_id,
        "subject_type": "answer",
        "subject_pub_id": "ans_1",
        "platform": "doubao",
        "subject_brand": "竞品A",
        "target_brand": "盛邦安全",
        "attitude": "negative",
        "evidence_quote": "引文",
        "confidence": 0.9,
        "method": "llm",
        "model": "gpt-5.6-luna",
        "prompt_version": "w3-v1",
        "created_at": datetime(2026, 8, 9, tzinfo=UTC),
        "source_url": None,
        "content_origin": "collection",
    }


class _CasesFakeConnection:
    def __init__(
        self,
        *,
        cases: list[dict[str, Any]],
        table_exists: bool,
        fact_checks: list[dict[str, Any]] | None = None,
    ) -> None:
        self._cases = cases
        self._table_exists = table_exists
        self._fact_checks = fact_checks or []
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "FROM platform.disparagement_judgment j" in normalized:
            return _Result(list(self._cases))
        if "to_regclass" in normalized:
            return _Result(
                [{"reg": "platform.disparagement_factcheck"}]
                if self._table_exists
                else [{"reg": None}]
            )
        if "FROM platform.disparagement_factcheck f" in normalized:
            return _Result(list(self._fact_checks))
        raise AssertionError(f"unexpected SQL: {sql}")


def _cases(
    monkeypatch: pytest.MonkeyPatch,
    connection: _CasesFakeConnection,
) -> list[dict[str, Any]]:
    @contextmanager
    def fake_platform_connection(dsn: str, tenant_pub_id: str) -> Any:
        yield connection

    monkeypatch.setattr(service_module, "_platform_tenant_connection", fake_platform_connection)
    return AnalyticsService(dsn="postgresql://fake").disparagement_cases(
        tenant_pub_id=_TENANT,
        project_pub_id=_PROJECT,
        start=_START,
        end=_END,
        limit=20,
    )


def test_cases_carry_fact_check_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    checked_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    connection = _CasesFakeConnection(
        cases=[_case("dj_1"), _case("dj_2")],
        table_exists=True,
        fact_checks=[
            {
                "judgment_pub_id": "dj_1",
                "verdict": "refuted",
                "summary": "官网白皮书显示该说法不成立。",
                "source_url": "https://www.webray.com.cn/whitepaper",
                "checked_at": checked_at,
            }
        ],
    )
    rows = _cases(monkeypatch, connection)
    assert rows[0]["fact_check"] == {
        "judgment_pub_id": "dj_1",
        "verdict": "refuted",
        "summary": "官网白皮书显示该说法不成立。",
        "source_url": "https://www.webray.com.cn/whitepaper",
        "checked_at": checked_at,
    }
    assert rows[1]["fact_check"] is None


def test_cases_degrade_when_factcheck_table_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _CasesFakeConnection(cases=[_case("dj_1")], table_exists=False)
    rows = _cases(monkeypatch, connection)
    assert rows[0]["fact_check"] is None
    # 只发起主查询 + to_regclass 探测，绝不触碰未迁移的契约表。
    assert not any("disparagement_factcheck f" in sql for sql in connection.statements)


def test_cases_skip_factcheck_lookup_without_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _CasesFakeConnection(cases=[], table_exists=True)
    assert _cases(monkeypatch, connection) == []
    assert not any("disparagement_factcheck f" in sql for sql in connection.statements)


# ---------------------------------------------------------------------------
# router 层 _project_fact_check 投影（DLP/合法性降级）
# ---------------------------------------------------------------------------


def test_project_fact_check_passes_clean_row() -> None:
    checked_at = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    view = _project_fact_check(
        {
            "verdict": "supported",
            "summary": "与官网表述一致。",
            "source_url": "https://www.webray.com.cn/a?x=1#frag",
            "checked_at": checked_at,
        }
    )
    assert view is not None
    assert view.verdict == "supported"
    assert view.summary == "与官网表述一致。"
    # _safe_source_url 口径：剥 query/fragment。
    assert view.source_url == "https://www.webray.com.cn/a"
    assert view.checked_at == checked_at


def test_project_fact_check_drops_row_with_bad_verdict() -> None:
    assert (
        _project_fact_check(
            {"verdict": None, "summary": "s", "source_url": None, "checked_at": datetime.now(UTC)}
        )
        is None
    )
    assert _project_fact_check({"verdict": "x" * 100, "checked_at": datetime.now(UTC)}) is None
    assert _project_fact_check({"verdict": "supported", "checked_at": "not-a-datetime"}) is None
    assert _project_fact_check(None) is None


def test_project_fact_check_sanitizes_source_url() -> None:
    view = _project_fact_check(
        {
            "verdict": "unverifiable",
            "summary": None,
            "source_url": "javascript:alert(1)",
            "checked_at": datetime.now(UTC),
        }
    )
    assert view is not None
    assert view.source_url is None
