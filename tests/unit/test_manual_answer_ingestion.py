"""人工补测登记通道（manual-ingestion-v1）单元测试。

fake 连接模式仿 tests/unit/test_analytics_comparisons.py：不起真 PG——
进程内 _TestClient + dependency_overrides[get_principal]；DB 访问全部走
fake：

- ``manual_ingestion.tenant_connection`` → fake CM + fake 连接，在 Python
  侧按 SQL 语义模拟 platform.project/brand/competitor 归属、
  evidence.evidence_asset 校验、analytics.answer 幂等预查与
  evidence.evidence_relation 关联插入；
- ``manual_ingestion.AnalyticsService.analyze_and_persist`` → 记录入参的
  fake（同构写入断言：provenance 五元/版本三元/dimensions 键集），并按需
  模拟「同键不同文 → replay payload drifted」。

覆盖：provenance 校验（MANUAL 适用性）、确定性 pub_id、eligible 旧路径
口径（无五元键 → eligible=true）、同构写入、幂等重复登记（created=false）、
端点鉴权 401/403/404/409/422、租户隔离、证据附件关联。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.analytics import manual_ingestion
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app

from domain.evidence.provenance import AccessClass, CaptureChannel, RedactedProvenance
from domain.scoring.eligibility import resolve_measurement_eligibility

TENANT = "tnt_manual"
OTHER_TENANT = "tnt_other"
PROJECT = "prj_manual"
NOW = datetime(2026, 9, 1, 16, 5, 0, tzinfo=UTC)

client = TestClient(app)

_EVIDENCE_ID = "evd_0123456789abcdef"


def _item(**overrides: Any) -> manual_ingestion.ManualAnswerItem:
    values: dict[str, Any] = {
        "model": "deepseek",
        "query_text": "家庭聚餐配什么果汁好",
        "response_plain_text": "家庭聚餐选果汁，推荐汇源 1L 装。",
        "capture_time": NOW,
        "region": "北京",
        "mode": "normal",
    }
    values.update(overrides)
    return manual_ingestion.ManualAnswerItem(**values)


# ── provenance / pub_id / eligible 纯函数层 ──────────────────────────────────
def test_provenance_manual_channel_accepted() -> None:
    provenance = RedactedProvenance(
        platform_account_pub_id=None,
        browser_profile_version_pub_id=None,
        session_event_pub_id=None,
        channel=CaptureChannel.MANUAL,
        authorization_scope=(),
        adapter_version=manual_ingestion.MANUAL_ADAPTER_VERSION,
        capture_time=NOW,
        access_class=AccessClass.CUSTOMER_PRIVATE,
    )
    assert provenance.channel.value == "manual"
    projection = provenance.public_projection()
    assert projection["channel"] == "manual"
    assert projection["platform_account_pub_id"] is None


def test_provenance_manual_rejects_authorized_session_capture() -> None:
    """authorized_session_capture 仍是 web 专属——manual 带 True 立即 fail-loud。"""
    with pytest.raises(ValueError, match="authorized session capture"):
        RedactedProvenance(
            platform_account_pub_id=None,
            browser_profile_version_pub_id=None,
            session_event_pub_id=None,
            channel=CaptureChannel.MANUAL,
            authorization_scope=(),
            adapter_version=manual_ingestion.MANUAL_ADAPTER_VERSION,
            capture_time=NOW,
            access_class=AccessClass.CUSTOMER_PRIVATE,
            authorized_session_capture=True,
        )


def test_manual_pub_id_deterministic_and_scoped() -> None:
    item = _item()
    first = manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id=PROJECT, item=item
    )
    again = manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id=PROJECT, item=item
    )
    assert first == again
    assert first.startswith("ans_") and len(first) == 30
    # 租户/项目/题文/capture_time/显式键任一变化 → 不同 pub_id。
    assert first != manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=OTHER_TENANT, project_pub_id=PROJECT, item=item
    )
    assert first != manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id="prj_other", item=item
    )
    assert first != manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id=PROJECT, item=_item(query_text="另一题")
    )
    assert first != manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id=PROJECT, item=_item(idempotency_key="batch-v1-01")
    )
    keyed = manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT, project_pub_id=PROJECT, item=_item(idempotency_key="batch-v1-01")
    )
    # 显式键下题文变化不改变身份（键即身份）。
    assert keyed == manual_ingestion.manual_answer_pub_id(
        tenant_pub_id=TENANT,
        project_pub_id=PROJECT,
        item=_item(query_text="另一题", idempotency_key="batch-v1-01"),
    )


def test_manual_dimensions_take_legacy_eligibility_path() -> None:
    """dimensions 无 INV-1 五元键 → 旧路径 eligible=true，且不补盖任何键。"""
    dimensions = manual_ingestion._manual_dimensions(
        operator="xln", reason="平台风控人工补测", item=_item()
    )
    assert "run_pub_id" not in dimensions
    assert "config_version_pub_id" not in dimensions
    for key in (
        "captcha_mode",
        "geo_source",
        "account_source",
        "rate_policy",
        "degraded_flag",
        "observed_gb_code",
    ):
        assert key not in dimensions
    eligible, degraded, stamped = resolve_measurement_eligibility(dimensions)
    assert eligible is True and degraded is False
    assert stamped == {key: str(value) for key, value in dimensions.items()}
    assert dimensions["channel"] == "manual"
    assert dimensions["manual_operator"] == "xln"
    assert dimensions["manual_reason"] == "平台风控人工补测"


# ── 服务层（fake 连接 + fake analyze_and_persist）────────────────────────────
class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeStore:
    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}  # pub_id → {id, tenant_pub_id}
        self.brands: dict[str, dict[str, Any]] = {}  # project_id → {name, website}
        self.competitors: dict[str, list[str]] = {}  # project_id → names
        self.evidence_assets: dict[str, str] = {}  # pub_id → tenant_pub_id
        self.answers: set[str] = set()  # 已存在的 answer pub_id（本租户）
        self.relations: list[tuple[str, str, str, str]] = []
        self.analyze_calls: list[dict[str, Any]] = []
        self.drift_on_call: int | None = None  # 第 N 次 analyze 调用抛 drift


class _FakeConnection:
    def __init__(self, store: _FakeStore, tenant_pub_id: str) -> None:
        self._store = store
        self._tenant = tenant_pub_id

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        params = params or ()
        normalized = " ".join(sql.split())
        store = self._store
        if "FROM platform.project" in normalized:
            (pub_id,) = params
            row = store.projects.get(pub_id)
            if row is None or row["tenant_pub_id"] != self._tenant:
                return _Result([])
            return _Result([{"id": row["id"]}])
        if "FROM platform.brand" in normalized:
            (project_id,) = params
            row = store.brands.get(project_id)
            return _Result([dict(row)] if row is not None else [])
        if "FROM platform.competitor" in normalized:
            (project_id,) = params
            return _Result([{"name": name} for name in store.competitors.get(project_id, [])])
        if "FROM evidence.evidence_asset" in normalized:
            tenant, pub_ids = params
            return _Result(
                [
                    {"pub_id": pub_id}
                    for pub_id in pub_ids
                    if store.evidence_assets.get(pub_id) == tenant
                ]
            )
        if "FROM analytics.answer" in normalized:
            tenant, pub_ids = params
            assert tenant == self._tenant
            return _Result([{"pub_id": p} for p in pub_ids if p in store.answers])
        if "INSERT INTO evidence.evidence_relation" in normalized:
            tenant, from_pub_id, to_pub_id, relation_type = params
            relation = (tenant, from_pub_id, to_pub_id, relation_type)
            if relation not in store.relations:  # ON CONFLICT DO NOTHING
                store.relations.append(relation)
            return _Result([])
        raise AssertionError(f"unexpected SQL: {normalized}")


class _FakeCM:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeConnection:
        return self._connection

    def __exit__(self, *args: Any) -> None:
        return None


class _FakeAnalyticsService:
    def __init__(self, store: _FakeStore) -> None:
        self._store = store

    def __call__(self, *, dsn: str) -> _FakeAnalyticsService:
        return self

    def analyze_and_persist(self, **kwargs: Any) -> dict[str, Any]:
        store = self._store
        call_index = len(store.analyze_calls) + 1
        store.analyze_calls.append(kwargs)
        if store.drift_on_call == call_index:
            raise ValueError("answer replay payload drifted")
        store.answers.add(str(kwargs["answer_pub_id"]))
        return {"analysis_pub_id": f"ana_{call_index:04d}"}


@pytest.fixture()
def store() -> _FakeStore:
    fake = _FakeStore()
    fake.projects[PROJECT] = {"id": "uuid-project", "tenant_pub_id": TENANT}
    fake.projects["prj_foreign"] = {"id": "uuid-foreign", "tenant_pub_id": OTHER_TENANT}
    fake.brands["uuid-project"] = {"name": "咱家果源", "website": "https://www.example.cn"}
    fake.competitors["uuid-project"] = ["汇源", "佳果源"]
    fake.evidence_assets[_EVIDENCE_ID] = TENANT
    return fake


@pytest.fixture()
def patched(store: _FakeStore, monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    monkeypatch.setattr(
        manual_ingestion,
        "tenant_connection",
        lambda dsn, tenant, **kwargs: _FakeCM(_FakeConnection(store, tenant)),
    )
    monkeypatch.setattr(manual_ingestion, "AnalyticsService", _FakeAnalyticsService(store))
    return store


def _register(**overrides: Any) -> list[manual_ingestion.ManualAnswerRegistration]:
    values: dict[str, Any] = {
        "tenant_pub_id": TENANT,
        "project_pub_id": PROJECT,
        "operator": "xln",
        "reason": "平台风控人工补测",
        "items": (_item(),),
    }
    values.update(overrides)
    return manual_ingestion.register_manual_answers("postgresql://fake", **values)


def test_register_writes_isomorphic_manual_row(patched: _FakeStore) -> None:
    registrations = _register()
    assert len(registrations) == 1
    registration = registrations[0]
    assert registration.created is True and registration.eligible is True
    call = patched.analyze_calls[0]
    # 同构写入：同一 analyze_and_persist 入口、同一版本三元、brand/competitors
    # 来自 platform 登记、provenance=channel manual + 三元 None + 原始 capture_time。
    assert call["tenant_pub_id"] == TENANT
    assert call["project_pub_id"] == PROJECT
    assert call["brand"] == "咱家果源"
    assert call["competitors"] == ("汇源", "佳果源")
    assert call["own_domains"] == ("https://www.example.cn",)
    assert call["scorer_version"] == "scorer-v2"
    assert call["metric_version"] == "metrics-v2"
    assert call["model_version"] == "rules-v1"
    provenance = call["provenance"]
    assert provenance.channel is CaptureChannel.MANUAL
    assert provenance.platform_account_pub_id is None
    assert provenance.session_event_pub_id is None
    assert provenance.capture_time == NOW
    assert provenance.adapter_version == manual_ingestion.MANUAL_ADAPTER_VERSION
    dimensions = call["dimensions"]
    assert dimensions["channel"] == "manual"
    assert dimensions["manual_operator"] == "xln"
    assert "run_pub_id" not in dimensions


def test_register_idempotent_replay_returns_existing(patched: _FakeStore) -> None:
    first = _register()[0]
    second = _register()[0]
    assert first.answer_pub_id == second.answer_pub_id
    assert first.created is True and second.created is False


def test_register_conflict_on_same_key_different_payload(patched: _FakeStore) -> None:
    _register()
    patched.drift_on_call = 2
    with pytest.raises(manual_ingestion.RegistrationConflict):
        _register(items=(_item(response_plain_text="另一份答案"),))


def test_register_unknown_project_raises(patched: _FakeStore) -> None:
    with pytest.raises(manual_ingestion.ProjectNotFound):
        _register(project_pub_id="prj_ghost")


def test_register_cross_tenant_project_not_visible(patched: _FakeStore) -> None:
    """他租户项目在本租户连接下不可见 → ProjectNotFound（同 404，不泄露存在性）。"""
    with pytest.raises(manual_ingestion.ProjectNotFound):
        _register(project_pub_id="prj_foreign")


def test_register_brand_missing_raises(patched: _FakeStore) -> None:
    patched.brands.clear()
    with pytest.raises(manual_ingestion.BrandMissing):
        _register()


def test_register_unknown_evidence_raises(patched: _FakeStore) -> None:
    with pytest.raises(manual_ingestion.UnknownEvidencePubId) as excinfo:
        _register(items=(_item(evidence_pub_ids=("evd_ffffffffffffffff",)),))
    assert excinfo.value.missing == ["evd_ffffffffffffffff"]


def test_register_links_existing_evidence(patched: _FakeStore) -> None:
    registrations = _register(items=(_item(evidence_pub_ids=(_EVIDENCE_ID,)),))
    assert registrations[0].evidence_attached == 1
    assert patched.relations == [
        (
            TENANT,
            registrations[0].answer_pub_id,
            _EVIDENCE_ID,
            manual_ingestion.MANUAL_EVIDENCE_RELATION_TYPE,
        )
    ]
    # 重登记幂等：关系不重复插。
    _register(items=(_item(evidence_pub_ids=(_EVIDENCE_ID,)),))
    assert len(patched.relations) == 1


def test_register_rejects_naive_capture_time(patched: _FakeStore) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _register(items=(_item(capture_time=datetime(2026, 9, 1, 16, 5, 0)),))


def test_register_rejects_empty_operator(patched: _FakeStore) -> None:
    with pytest.raises(ValueError, match="operator"):
        _register(operator="  ")


# ── 端点层 ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _cleanup_overrides() -> Any:
    yield
    app.dependency_overrides.pop(get_principal, None)


def _override_principal(role: Role = Role.OPERATOR, tenant: str = TENANT) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-manual", role=role, tenant_pub_id=tenant, user_pub_id="usr_manual"
    )


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "project_pub_id": PROJECT,
        "operator": "xln",
        "reason": "平台风控人工补测",
        "items": [
            {
                "model": "deepseek",
                "query_text": "家庭聚餐配什么果汁好",
                "response_plain_text": "家庭聚餐选果汁，推荐汇源 1L 装。",
                "capture_time": "2026-09-01T16:05:00+08:00",
                "region": "北京",
                "mode": "normal",
            }
        ],
    }
    body.update(overrides)
    return body


def _post(body: dict[str, Any]) -> Any:
    return client.post("/api/v2/analytics/manual-answers", json=body)


def _stub_register(
    monkeypatch: pytest.MonkeyPatch,
    registrations: list[manual_ingestion.ManualAnswerRegistration] | None = None,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake(dsn: str, **kwargs: Any) -> list[manual_ingestion.ManualAnswerRegistration]:
        captured.update(kwargs)
        return registrations or [
            manual_ingestion.ManualAnswerRegistration(
                answer_pub_id="ans_stub",
                analysis_pub_id="ana_stub",
                created=True,
                eligible=True,
                evidence_attached=0,
            )
        ]

    monkeypatch.setattr(manual_ingestion, "register_manual_answers", fake)
    return captured


def test_endpoint_requires_authentication_401() -> None:
    assert _post(_body()).status_code == 401


def test_endpoint_permission_denied_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """customer 角色无 project:write → 403，门在服务层之前。"""
    _override_principal(Role.CUSTOMER)
    monkeypatch.setattr(
        manual_ingestion,
        "register_manual_answers",
        lambda *a, **k: pytest.fail("越权访问不应触达服务层"),
    )
    resp = _post(_body())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


def test_endpoint_shape_validation_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    monkeypatch.setattr(
        manual_ingestion,
        "register_manual_answers",
        lambda *a, **k: pytest.fail("形状错误不应触达服务层"),
    )
    assert _post(_body(items=[])).status_code == 422
    assert _post(_body(unknown_field=1)).status_code == 422  # extra=forbid
    assert _post(_body(operator="")).status_code == 422
    naive = _body()
    naive["items"][0]["capture_time"] = "2026-09-01T16:05:00"
    assert _post(naive).status_code == 422  # 无时区
    bad_evidence = _body()
    bad_evidence["items"][0]["evidence_pub_ids"] = ["not-an-evd"]
    assert _post(bad_evidence).status_code == 422


def test_endpoint_happy_path_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    captured = _stub_register(monkeypatch)
    resp = _post(_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_pub_id"] == PROJECT
    assert body["registered"] == 1
    assert body["items"][0]["answer_pub_id"] == "ans_stub"
    assert body["items"][0]["created"] is True
    assert body["items"][0]["eligible"] is True
    item = captured["items"][0]
    assert item.capture_time.tzinfo is not None
    assert item.region == "北京"


def test_endpoint_project_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()

    def fake(dsn: str, **kwargs: Any) -> Any:
        raise manual_ingestion.ProjectNotFound("project_not_found")

    monkeypatch.setattr(manual_ingestion, "register_manual_answers", fake)
    resp = _post(_body())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_endpoint_brand_missing_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()

    def fake(dsn: str, **kwargs: Any) -> Any:
        raise manual_ingestion.BrandMissing("manual_answer_brand_missing")

    monkeypatch.setattr(manual_ingestion, "register_manual_answers", fake)
    resp = _post(_body())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "manual_answer_brand_missing"


def test_endpoint_unknown_evidence_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()

    def fake(dsn: str, **kwargs: Any) -> Any:
        raise manual_ingestion.UnknownEvidencePubId(["evd_ffffffffffffffff"])

    monkeypatch.setattr(manual_ingestion, "register_manual_answers", fake)
    body = _body()
    body["items"][0]["evidence_pub_ids"] = ["evd_ffffffffffffffff"]
    resp = _post(body)
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["error"]["code"] == "unknown_evidence_pub_id"
    assert payload["error"]["details"]["pub_ids"] == ["evd_ffffffffffffffff"]


def test_endpoint_payload_drift_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()

    def fake(dsn: str, **kwargs: Any) -> Any:
        raise manual_ingestion.RegistrationConflict("ans_drift")

    monkeypatch.setattr(manual_ingestion, "register_manual_answers", fake)
    resp = _post(_body())
    assert resp.status_code == 409
    payload = resp.json()
    assert payload["error"]["code"] == "manual_answer_payload_drift"
    assert payload["error"]["details"]["answer_pub_id"] == "ans_drift"
