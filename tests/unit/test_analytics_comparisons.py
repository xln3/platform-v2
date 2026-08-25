"""analytics run 组对比端点（报价单服务④，brandrank 层口径）单元测试。

fake 连接模式仿 tests/unit/test_analytics_delta.py：不起真 PG——进程内
_TestClient + dependency_overrides[get_principal]；DB 访问全部走 fake：

- ``comparisons.tenant_connection`` / ``brandrank_compare.tenant_connection`` /
  ``analytics_service._platform_tenant_connection`` → fake CM + fake 连接，
  在 Python 侧按 SQL 语义模拟 analytics.run_comparison（INSERT/SELECT）、
  platform.collection_run 归属校验、analytics.answer 谓词（eligible AND NOT
  degraded + run_pub_id = ANY，防谓词回退——漏过滤会改变手工核算的期望值）；
- ``brandrank_service.fetch_project`` / ``fetch_brand_extracts`` → 假项目/假抽取表
  （照 test_report_before_after.py 同款接缝）。

覆盖：创建校验错误（422 形状 / 400 unknown_run_pub_id / 403 / 401）、逐题配对数学
（before/after/delta 与手工算一致）、unpaired 列出、臂空 → insufficient 诚实占位、
同输入下 aggregate 与报告 before_after 扩展组同数（同一构造函数的接线证明）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.analytics import comparisons
from geo_platform.analytics import service as analytics_service
from geo_platform.brandrank import compare as brandrank_compare
from geo_platform.brandrank import service as brandrank_service
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app
from geo_platform.reports import fact_suggestions

TENANT = "tnt_cmp"
PROJECT = "prj_cmp"
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)

client = TestClient(app)

_PROJECT = {
    "pub_id": PROJECT,
    "name": "盛邦验证",
    "brandrank_domain": "cybersecurity",
    "brand_names": ["盛邦安全"],
    "competitor_names": ["奇安信"],
}


# ── fake 连接（按 SQL 语义在 Python 侧模拟三张表）────────────────────────────
class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeStore:
    """跨 fake 连接共享的库内状态：comparison 行 / run 归属 / answer 行。"""

    def __init__(self) -> None:
        self.comparisons: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, str] = {}  # run_pub_id → project_pub_id（本租户）
        self.answers: list[dict[str, Any]] = []  # 含 tenant/project/eligible/degraded/run_pub_id
        self._tick = 0

    def next_created_at(self) -> datetime:
        self._tick += 1
        return NOW + timedelta(seconds=self._tick)


def _answer(
    pub_id: str, run_pub_id: str, query: str, *, eligible: bool = True, degraded: bool = False
) -> dict[str, Any]:
    return {
        "pub_id": pub_id,
        "tenant_pub_id": TENANT,
        "project_pub_id": PROJECT,
        "run_pub_id": run_pub_id,
        "query_text": query,
        "response_text": "r",
        "model": "doubao",
        "region": "北京",
        "mode": "normal",
        "eligible": eligible,
        "degraded": degraded,
        "capture_time": NOW,
    }


_ANSWER_PROJECTION = (
    "pub_id",
    "query_text",
    "response_text",
    "model",
    "region",
    "mode",
    "capture_time",
)


class _FakeConnection:
    """按 comparisons/compare 的 SQL 语义模拟：run_comparison CRUD +
    collection_run 归属 + answer eligible/run 谓词。"""

    def __init__(self, store: _FakeStore, tenant_pub_id: str) -> None:
        self._store = store
        self._tenant = tenant_pub_id

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        params = params or ()
        normalized = " ".join(sql.split())
        if "INSERT INTO analytics.run_comparison" in normalized:
            (pub_id, tenant, project, name, baseline_json, optimized_json, note, created_by) = (
                params
            )
            row = {
                "pub_id": pub_id,
                "tenant_pub_id": tenant,
                "project_pub_id": project,
                "name": name,
                "baseline_run_pub_ids": json.loads(baseline_json),
                "optimized_run_pub_ids": json.loads(optimized_json),
                "note": note,
                "created_by": created_by,
                "created_at": self._store.next_created_at(),
            }
            self._store.comparisons[pub_id] = row
            return _Result([self._entity(row)])
        if "FROM analytics.run_comparison" in normalized:
            if "ORDER BY created_at DESC" in normalized:
                tenant, project, cursor_created_at, _cursor_created_at, cursor_pub_id, limit = (
                    params
                )
                rows = [
                    r
                    for r in self._store.comparisons.values()
                    if r["tenant_pub_id"] == tenant
                    and r["project_pub_id"] == project
                    and (
                        cursor_created_at is None
                        or (r["created_at"], r["pub_id"]) < (cursor_created_at, cursor_pub_id)
                    )
                ]
                rows.sort(key=lambda r: (r["created_at"], r["pub_id"]), reverse=True)
                return _Result([self._entity(r) for r in rows[:limit]])
            tenant, pub_id = params
            row = self._store.comparisons.get(pub_id)
            if row is None or row["tenant_pub_id"] != tenant:
                return _Result([])
            return _Result([self._entity(row)])
        if "FROM platform.collection_run" in normalized:
            project, requested = params
            found = [pub_id for pub_id in requested if self._store.runs.get(pub_id) == project]
            return _Result([{"pub_id": pub_id} for pub_id in found])
        if "FROM analytics.answer" in normalized:
            tenant, project, run_pub_ids, limit = params
            rows = [
                {key: answer[key] for key in _ANSWER_PROJECTION}
                for answer in self._store.answers
                if answer["tenant_pub_id"] == tenant
                and answer["project_pub_id"] == project
                and answer["eligible"]
                and not answer["degraded"]
                and answer["run_pub_id"] in run_pub_ids
            ]
            rows.sort(key=lambda r: (r["capture_time"], r["pub_id"]))
            return _Result(rows[:limit])
        raise AssertionError(f"unexpected SQL: {normalized}")

    @staticmethod
    def _entity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "pub_id",
                "project_pub_id",
                "name",
                "baseline_run_pub_ids",
                "optimized_run_pub_ids",
                "note",
                "created_by",
                "created_at",
            )
        }


class _FakeCM:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeConnection:
        return self._connection

    def __exit__(self, *args: Any) -> None:
        return None


# ── 共享 fixtures/接缝 ───────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _cleanup_overrides() -> Any:
    yield
    app.dependency_overrides.pop(get_principal, None)


def _override_principal(
    role: Role = Role.OPERATOR, tenant: str = TENANT, user_pub_id: str | None = "usr_cmp"
) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-cmp", role=role, tenant_pub_id=tenant, user_pub_id=user_pub_id
    )


def _patch_db(monkeypatch: pytest.MonkeyPatch, store: _FakeStore) -> None:
    """把三个连接接缝指到 fake 连接工厂（共享 store）。"""

    def fake_tenant_connection(dsn: str, tenant_pub_id: str, **kwargs: Any) -> _FakeCM:
        return _FakeCM(_FakeConnection(store, tenant_pub_id))

    monkeypatch.setattr(comparisons, "tenant_connection", fake_tenant_connection)
    monkeypatch.setattr(brandrank_compare, "tenant_connection", fake_tenant_connection)
    monkeypatch.setattr(
        analytics_service,
        "_platform_tenant_connection",
        lambda dsn, tenant: _FakeCM(_FakeConnection(store, tenant)),
    )


def _patch_project_and_extracts(
    monkeypatch: pytest.MonkeyPatch,
    table: dict[str, dict[str, Any]],
    project: dict[str, Any] | None = _PROJECT,
) -> None:
    monkeypatch.setattr(
        brandrank_service,
        "fetch_project",
        lambda dsn, tenant, project_pub_id: (dict(project) if project is not None else None),
    )
    monkeypatch.setattr(
        brandrank_service,
        "fetch_brand_extracts",
        lambda dsn, tenant, ids, domain: {i: table[i] for i in ids if i in table},
    )


def _post(body: dict[str, Any], **kwargs: Any) -> Any:
    return client.post("/api/v2/analytics/comparisons", json=body, **kwargs)


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "project_pub_id": PROJECT,
        "name": "盛邦基线 vs 优化",
        "baseline_run_pub_ids": ["run_base1"],
        "optimized_run_pub_ids": ["run_opt1"],
        "note": "首轮",
    }
    body.update(overrides)
    return body


# ── 创建校验 ─────────────────────────────────────────────────────────────────
def test_post_requires_authentication_401() -> None:
    assert _post(_create_body()).status_code == 401


def test_post_permission_denied_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """customer 角色无 schedule:manage（operator/admin 级写权限）→ 403，门在 DB 之前。"""
    _override_principal(Role.CUSTOMER)
    monkeypatch.setattr(
        comparisons, "validate_project_runs", lambda *a, **k: pytest.fail("越权访问不应触达 DB")
    )
    resp = _post(_create_body())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


def test_post_shape_validation_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    monkeypatch.setattr(
        comparisons,
        "validate_project_runs",
        lambda *a, **k: pytest.fail("形状错误不应触达归属校验"),
    )
    assert _post(_create_body(baseline_run_pub_ids=[])).status_code == 422
    assert _post(_create_body(optimized_run_pub_ids=[])).status_code == 422
    assert _post(_create_body(baseline_run_pub_ids=["not-a-run"])).status_code == 422
    assert _post(_create_body(name="")).status_code == 422
    assert _post(_create_body(unknown_field=1)).status_code == 422  # extra=forbid


def test_post_unknown_run_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """run 不存在 / 属于其他项目 → 400 unknown_run_pub_id（逐个列出，不泄露归属）。"""
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT, "run_other": "prj_other"})
    _patch_db(monkeypatch, store)
    resp = _post(
        _create_body(
            baseline_run_pub_ids=["run_base1", "run_ghost"], optimized_run_pub_ids=["run_other"]
        )
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "unknown_run_pub_id"
    assert body["error"]["details"]["unknown_run_pub_ids"] == ["run_ghost", "run_other"]


def test_post_create_and_list_201(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT})
    _patch_db(monkeypatch, store)

    headers = {"Idempotency-Key": "cmp-test-1234567890abcdef"}
    resp = _post(_create_body(), headers=headers)
    assert resp.status_code == 201
    assert resp.headers["Idempotency-Key"] == headers["Idempotency-Key"]  # 回显
    entity = resp.json()
    assert entity["pub_id"].startswith("rcmp_")
    assert entity["project_pub_id"] == PROJECT
    assert entity["baseline_run_pub_ids"] == ["run_base1"]
    assert entity["optimized_run_pub_ids"] == ["run_opt1"]
    assert entity["note"] == "首轮" and entity["created_by"] == "usr_cmp"
    assert store.comparisons[entity["pub_id"]]["tenant_pub_id"] == TENANT  # RLS 列落账

    resp2 = _post(_create_body(name="第二轮"))
    assert resp2.status_code == 201
    listed = client.get(f"/api/v2/analytics/comparisons?project_pub_id={PROJECT}")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["name"] for item in items] == ["第二轮", "盛邦基线 vs 优化"]  # 倒序
    # 列表只含本 tenant+project 行
    assert all(item["project_pub_id"] == PROJECT for item in items)


def test_get_unknown_comparison_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    _patch_db(monkeypatch, _FakeStore())
    resp = client.get("/api/v2/analytics/comparisons/rcmp_ghost")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "comparison_not_found"


# ── 结果计算：逐题配对 / unpaired / aggregate 与扩展组同数 ─────────────────────
_BASELINE_ANSWERS = [
    _answer("ans_b1", "run_base1", "网络安全厂商推荐"),
    _answer("ans_b2", "run_base1", "网络安全厂商推荐"),
    _answer("ans_b3", "run_base1", "仅基线题"),
    _answer("ans_b4", "run_base1", "覆盖缺失题"),
    # 谓词守卫：ineligible / 其他 run 的同题答案绝不计入（泄漏即改变手工核算值）
    _answer("ans_bx", "run_base1", "网络安全厂商推荐", eligible=False),
    _answer("ans_bo", "run_other", "网络安全厂商推荐"),
]
_OPTIMIZED_ANSWERS = [
    _answer("ans_a1", "run_opt1", "网络安全厂商推荐"),
    _answer("ans_a2", "run_opt1", "网络安全厂商推荐"),
    _answer("ans_a3", "run_opt1", "仅优化题"),
    _answer("ans_a4", "run_opt1", "覆盖缺失题"),
]
_EXTRACTS = {
    "ans_b1": {"status": "ok", "brands": ["奇安信"]},
    "ans_b2": {"status": "ok", "brands": ["奇安信", "盛邦安全"]},
    "ans_b3": {"status": "ok", "brands": ["盛邦安全"]},
    "ans_b4": {"status": "failed", "brands": []},  # 该题基线臂零覆盖
    "ans_bx": {"status": "ok", "brands": ["盛邦安全"]},
    "ans_bo": {"status": "ok", "brands": ["盛邦安全"]},
    "ans_a1": {"status": "ok", "brands": ["盛邦安全", "奇安信"]},
    "ans_a2": {"status": "ok", "brands": ["盛邦安全"]},
    "ans_a3": {"status": "ok", "brands": ["奇安信"]},
    "ans_a4": {"status": "ok", "brands": ["盛邦安全"]},
}


def _seeded_comparison(monkeypatch: pytest.MonkeyPatch) -> str:
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT, "run_other": "prj_other"})
    store.answers.extend(_BASELINE_ANSWERS + _OPTIMIZED_ANSWERS)
    _patch_db(monkeypatch, store)
    _patch_project_and_extracts(monkeypatch, _EXTRACTS)
    resp = _post(_create_body())
    assert resp.status_code == 201
    return resp.json()["pub_id"]


def _result(pub_id: str) -> dict[str, Any]:
    resp = client.get(f"/api/v2/analytics/comparisons/{pub_id}")
    assert resp.status_code == 200
    return resp.json()


def test_result_pairing_math_and_unpaired(monkeypatch: pytest.MonkeyPatch) -> None:
    pub_id = _seeded_comparison(monkeypatch)
    body = _result(pub_id)
    assert body["baseline_run_pub_ids"] == ["run_base1"]
    result = body["result"]
    assert result["status"] == "ok" and result["insufficient_reasons"] == []
    assert result["domain"] == "cybersecurity"
    assert result["target_brand"] == "盛邦安全"
    assert result["coverage"]["before_answers"] == 4  # ans_bx/ans_bo 被谓词剔除
    assert result["coverage"]["before_with_extract"] == 3  # ans_b4 failed 不入记录

    questions = {q["query_text"]: q for q in result["questions"]}
    assert set(questions) == {"网络安全厂商推荐", "覆盖缺失题"}

    q1 = questions["网络安全厂商推荐"]
    assert q1["status"] == "ok"
    # before：2 条记录，提及 1 次 rank2 → mention 50.0 / avg 2.0 / top1 0.0
    assert q1["before"]["mention_rate"] == {
        "value": 50.0,
        "unit": "percent",
        "numerator": 1,
        "denominator": 2,
    }
    assert q1["before"]["avg_rank"]["value"] == 2.0
    assert q1["before"]["top1"]["value"] == 0.0
    assert q1["before"]["top1"]["of_mentions"] == 0.0
    # after：2 条记录，提及 2 次均 rank1 → 100.0 / 1.0 / top1 100.0
    assert q1["after"]["mention_rate"]["value"] == 100.0
    assert q1["after"]["avg_rank"]["value"] == 1.0
    assert q1["after"]["top1"]["value"] == 100.0
    assert q1["delta"] == {
        "mention_rate": 50.0,
        "avg_rank": -1.0,
        "top1": 100.0,
        "top3": 50.0,
        "top5": 50.0,
    }

    q4 = questions["覆盖缺失题"]  # 基线臂问过但零覆盖 → 诚实占位
    assert q4["status"] == "insufficient"
    assert q4["insufficient_reasons"] == ["before_no_extraction_coverage"]
    assert q4["before"] is None
    assert q4["after"]["mention_rate"]["value"] == 100.0  # 有覆盖臂照实出数
    assert q4["delta"] == {
        "mention_rate": None,
        "avg_rank": None,
        "top1": None,
        "top3": None,
        "top5": None,
    }

    assert result["unpaired"] == {"baseline_only": ["仅基线题"], "optimized_only": ["仅优化题"]}


def test_result_aggregate_hand_math(monkeypatch: pytest.MonkeyPatch) -> None:
    """组级 aggregate 手工核算：before 3 条记录 / after 4 条记录
    （q4 基线 failed 剔除、ineligible/异 run 谓词剔除）。"""
    pub_id = _seeded_comparison(monkeypatch)
    metrics_rows = {
        r["extra"]["metric_name"]: r for r in _result(pub_id)["result"]["aggregate"]["metrics"]
    }
    assert set(metrics_rows) == {"mention_rate", "avg_rank", "top1", "top3", "top5"}

    mention = metrics_rows["mention_rate"]
    # before：b1 未提及 + b2 rank2 + b3 rank1 → 2/3；
    # after：a1 rank1 + a2 rank1 + a3 未提及 + a4 rank1 → 3/4
    assert mention["extra"]["before"] == 66.67 and mention["extra"]["after"] == 75.0
    assert mention["value"] == 8.33  # after − before
    assert mention["extra"]["denominators"] == {"before_n": 3, "after_n": 4}
    assert mention["numerator"] == 3 and mention["denominator"] == 4
    assert mention["extra"]["before_numerator"] == 2
    assert mention["method"] == "brandrank-llm-v1" and mention["domain"] == "cybersecurity"

    rank = metrics_rows["avg_rank"]
    assert rank["extra"]["before"] == 1.5 and rank["extra"]["after"] == 1.0
    assert rank["value"] == -0.5 and rank["unit"] == "rank"

    top1 = metrics_rows["top1"]
    assert top1["extra"]["before"] == 33.33 and top1["extra"]["after"] == 75.0
    assert top1["value"] == 41.67
    assert top1["extra"]["before_of_mentions"] == 50.0  # 1/2 提及 ≤1
    assert top1["extra"]["after_of_mentions"] == 100.0

    top3 = metrics_rows["top3"]
    assert top3["extra"]["before"] == 66.67 and top3["extra"]["after"] == 75.0
    assert top3["value"] == 8.33
    assert top3["extra"]["before_of_mentions"] == 100.0

    top5 = metrics_rows["top5"]
    assert top5["extra"]["before"] == 66.67 and top5["extra"]["after"] == 75.0
    assert top5["value"] == 8.33


def test_result_aggregate_matches_report_before_after_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同输入下：端点 aggregate 与报告 before_after 扩展组逐指标同数（同一构造函数的
    接线证明——扩展组路径经 fetch_answers_window 接缝喂同一批臂数据）。"""
    pub_id = _seeded_comparison(monkeypatch)
    endpoint_rows = {
        r["extra"]["metric_name"]: r for r in _result(pub_id)["result"]["aggregate"]["metrics"]
    }

    # 报告扩展组路径：主草稿/W2/W3 垫底为空，before/after 双臂按窗喂同一批答案
    monkeypatch.setattr(
        brandrank_service, "fetch_answers", lambda dsn, tenant, project, since: ([], False)
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_disparagement_judgments",
        lambda dsn, tenant, project, since, until: ([], False),
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_source_audit_overview",
        lambda dsn, tenant, project, start, end: {
            "own_site_host": None,
            "documents_total": 0,
            "own_site_documents": 0,
            "own_site_share": None,
        },
    )
    monkeypatch.setattr(
        fact_suggestions,
        "fetch_site_audit_suggestions",
        lambda dsn, tenant, project: {"rows": [], "batch_pub_id": None, "truncated": False},
    )

    def fake_window(
        dsn: str, tenant: str, project: str, start: datetime, end: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        # 与 run 版 fake 同一臂集合（同 eligible/run 谓词），投影形状与真 SQL 一致
        run = "run_base1" if start.month == 7 else "run_opt1"
        return [
            {key: a[key] for key in _ANSWER_PROJECTION}
            for a in _BASELINE_ANSWERS + _OPTIMIZED_ANSWERS
            if a["run_pub_id"] == run and a["eligible"] and not a["degraded"]
        ], False

    monkeypatch.setattr(fact_suggestions, "fetch_answers_window", fake_window)
    section = fact_suggestions.compute_report_fact_suggestions(
        dsn="postgresql://fake",
        tenant_pub_id=TENANT,
        project_pub_id=PROJECT,
        window_days=7,
        now=NOW,
        before_start="2026-07-01",
        before_end="2026-07-07",
        after_start="2026-08-01",
        after_end="2026-08-07",
    )["before_after"]
    assert section["status"] == "ok"
    section_rows = {r["extra"]["metric_name"]: r for r in section["fact_rows"]}

    assert set(endpoint_rows) == set(section_rows)
    for name, row in section_rows.items():
        endpoint_row = endpoint_rows[name]
        assert endpoint_row["metric"] == row["metric"] == "before_after_metric"
        assert endpoint_row["value"] == row["value"]
        assert endpoint_row["unit"] == row["unit"]
        assert endpoint_row["numerator"] == row["numerator"]
        assert endpoint_row["denominator"] == row["denominator"]
        assert endpoint_row["method"] == row["method"]
        assert endpoint_row["domain"] == row["domain"]
        for key in (
            "before",
            "after",
            "before_numerator",
            "denominators",
            "before_of_mentions",
            "after_of_mentions",
        ):
            assert endpoint_row["extra"].get(key) == row["extra"].get(key), f"{name}.{key}"


# ── 诚实边界 ─────────────────────────────────────────────────────────────────
def test_result_insufficient_when_arm_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """基线臂零答案 → status=insufficient + before_no_answers，aggregate 空绝不伪零。"""
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT})
    store.answers.extend(_OPTIMIZED_ANSWERS)  # 只有优化臂有数据
    _patch_db(monkeypatch, store)
    _patch_project_and_extracts(monkeypatch, _EXTRACTS)
    pub_id = _post(_create_body()).json()["pub_id"]

    result = _result(pub_id)["result"]
    assert result["status"] == "insufficient"
    assert result["insufficient_reasons"] == ["before_no_answers"]
    assert result["aggregate"]["metrics"] == []  # INV-32 零合成
    assert result["questions"] == []  # 无答案级配对题
    assert result["unpaired"] == {
        "baseline_only": [],
        "optimized_only": ["仅优化题", "网络安全厂商推荐", "覆盖缺失题"],
    }  # sorted 按码位序
    assert result["coverage"]["before_answers"] == 0


def test_result_domain_unset_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """项目未设 brandrank_domain → 400 domain_unset（不回退缺省包，同报告口径）。"""
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT})
    _patch_db(monkeypatch, store)
    _patch_project_and_extracts(
        monkeypatch, _EXTRACTS, project={**_PROJECT, "brandrank_domain": None}
    )
    pub_id = _post(_create_body()).json()["pub_id"]
    resp = client.get(f"/api/v2/analytics/comparisons/{pub_id}")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "domain_unset"


def test_result_project_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """实体存在但项目被删（创建后）→ 404 project_not_found。"""
    _override_principal()
    store = _FakeStore()
    store.runs.update({"run_base1": PROJECT, "run_opt1": PROJECT})
    _patch_db(monkeypatch, store)
    _patch_project_and_extracts(monkeypatch, _EXTRACTS, project=None)
    pub_id = _post(_create_body()).json()["pub_id"]
    resp = client.get(f"/api/v2/analytics/comparisons/{pub_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
