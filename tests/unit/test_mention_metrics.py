"""domain.reporting.mention_metrics（mention-metrics-v1）纯函数单测 + POST 端点门测试。

口径逐条锚定源脚本 clients/client-zjgy/recompute_zjgy_report_data.py：cell 取
最新 N 条、名称命中=精确子串、occ=count、信源聚合（host/url 为 None 不计
domains/urls 但计 records）、品牌页面追踪、零覆盖诚实空结构、spec_hash 稳定。
API 测试全 fake：monkeypatch analytics.mention_metrics.compute_for_project 接缝，
绝不触 DB（照 test_report_fact_suggestions 模式）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from geo_platform.analytics import mention_metrics as mention_metrics_service
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app

from domain.reporting.mention_metrics import (
    MENTION_METRICS_VERSION,
    AnswerRow,
    CitationRow,
    MentionFlag,
    MentionMetricsSpec,
    QueryGroup,
    QueryItem,
    WaveSpec,
    compute_mention_metrics,
    spec_hash,
    spec_payload,
    wave_spec_from_truth,
)

TENANT = "tnt_mention"
PROJECT = "prj_mention"

client = TestClient(app)


def _spec() -> MentionMetricsSpec:
    return MentionMetricsSpec(
        platforms=("deepseek", "tongyi"),
        samples_per_query={"deepseek": 1, "tongyi": 2},
        names=("咱家果源", "汇源"),
        brands=("汇源", "咱家果源"),
        terms=("NFC",),
        primary_name="咱家果源",
        waves=(
            WaveSpec(
                wave="w1",
                query_groups=(
                    QueryGroup(
                        name="ZJ-Q003｜品类发现｜NFC果汁候选集",
                        items=(
                            QueryItem(text="问题一", priority=1),
                            QueryItem(text="问题二", priority=2),
                        ),
                    ),
                ),
            ),
            WaveSpec(
                wave="w2",
                query_groups=(
                    QueryGroup(
                        name="ZJ-Q001｜品牌背景｜股权",
                        items=(QueryItem(text="背景问题", priority=1),),
                    ),
                ),
            ),
        ),
        mention_flags=(MentionFlag(name="汇源", key="has_huiyuan"),),
        excerpt_groups=("ZJ-Q001",),
    )


def _answer(
    pub: str,
    *,
    model: str,
    q: str,
    resp: str,
    cap: str,
) -> AnswerRow:
    return AnswerRow(pub=pub, run=None, model=model, q=q, resp=resp, cap=cap)


def _answers() -> list[AnswerRow]:
    ds_resp = "x" * 200 + "咱家果源是汇源系新品牌，咱家果源主打NFC。" + "y" * 300
    return [
        # cell (w1, deepseek, 问题一)：3 条采 1 条（canary/retry 2 条应丢弃）
        _answer(
            "ans_ds_old1",
            model="deepseek",
            q="问题一",
            resp="旧样本",
            cap="2026-08-31 09:00:00+08:00",
        ),
        _answer(
            "ans_ds_old2",
            model="deepseek",
            q="问题一",
            resp="次新样本",
            cap="2026-08-31 10:00:00+08:00",
        ),
        _answer(
            "ans_ds", model="deepseek", q="问题一", resp=ds_resp, cap="2026-08-31 11:00:00+08:00"
        ),
        # cell (w1, tongyi, 问题一)：采样 2 条全保留
        _answer(
            "ans_ty1",
            model="tongyi",
            q="问题一",
            resp="推荐咱家果源。",
            cap="2026-08-31 09:30:00+08:00",
        ),
        _answer(
            "ans_ty2",
            model="tongyi",
            q="问题一",
            resp="没有提到任何品牌。",
            cap="2026-08-31 10:30:00+08:00",
        ),
        # cell (w1, tongyi, 问题二)：1 条
        _answer(
            "ans_ty3",
            model="tongyi",
            q="问题二",
            resp="汇源与汇源果汁。",
            cap="2026-08-31 09:45:00+08:00",
        ),
        # 真源外查询：逐字匹配不上，绝不计入
        _answer(
            "ans_noise",
            model="deepseek",
            q="无关问题",
            resp="咱家果源",
            cap="2026-08-31 12:00:00+08:00",
        ),
        # W2 cells
        _answer(
            "ans_w2_ds",
            model="deepseek",
            q="背景问题",
            resp="NFC是浓缩还原之外路线。",
            cap="2026-08-31 09:10:00+08:00",
        ),
        _answer(
            "ans_w2_ty1",
            model="tongyi",
            q="背景问题",
            resp="无法核实相关信息。",
            cap="2026-08-31 09:20:00+08:00",
        ),
        _answer(
            "ans_w2_ty2",
            model="tongyi",
            q="背景问题",
            resp="汇源集团背景。",
            cap="2026-08-31 09:25:00+08:00",
        ),
    ]


def _citations() -> list[CitationRow]:
    return [
        CitationRow(
            answer_pub_id="ans_ds",
            host="example.com",
            canonical_url="https://example.com/a",
            title="咱家果源官网",
            cited_text="正文",
        ),
        CitationRow(
            answer_pub_id="ans_ds",
            host="example.com",
            canonical_url="https://example.com/b",
            title="其他",
            cited_text="正文",
        ),
        # host/url 为 None：计 records 但不计 domains/urls；cited_text 含品牌名 → 品牌页面
        CitationRow(
            answer_pub_id="ans_ty1",
            host=None,
            canonical_url=None,
            title=None,
            cited_text="咱家果源摘录",
        ),
        CitationRow(
            answer_pub_id="ans_ty3",
            host="example.com",
            canonical_url="https://example.com/a",
            title="重复域",
            cited_text="正文",
        ),
        CitationRow(
            answer_pub_id="ans_ty3",
            host="other.com",
            canonical_url="https://other.com/x",
            title="外部",
            cited_text="正文",
        ),
        # 被 cell 去重丢弃的答案与 W2 答案的 citation：绝不进 W1 信源统计
        CitationRow(
            answer_pub_id="ans_ds_old1",
            host="dropped.com",
            canonical_url="https://dropped.com/",
            title=None,
            cited_text=None,
        ),
        CitationRow(
            answer_pub_id="ans_w2_ds",
            host="w2.com",
            canonical_url="https://w2.com/",
            title=None,
            cited_text=None,
        ),
    ]


# ── 纯函数口径 ───────────────────────────────────────────────────────────────
def test_cell_dedup_latest_n_and_dropped_count() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    assert out["metric_version"] == MENTION_METRICS_VERSION
    assert out["dropped_extra_answers"] == 2
    assert out["w1_total"] == 4
    assert out["w2_total"] == 3
    # deepseek 只保留最新一条（含双咱家果源的 resp），旧样本不进场
    assert out["platform_coverage"]["deepseek"] == {
        "w1_answers": 1,
        "w1_queries": 1,
        "w2_answers": 1,
        "w2_queries": 1,
    }
    assert out["platform_coverage"]["tongyi"] == {
        "w1_answers": 3,
        "w1_queries": 2,
        "w2_answers": 2,
        "w2_queries": 1,
    }


def test_platform_samples_map_respected() -> None:
    """tongyi 采样数 2：cell 内 2 条全保留；改为 1 则丢 1 条且只留最新。"""
    spec = _spec()
    out = compute_mention_metrics(spec=spec, answers=_answers(), citations=[])
    assert out["platform_coverage"]["tongyi"]["w1_answers"] == 3
    tightened = MentionMetricsSpec(
        **{**spec.__dict__, "samples_per_query": {"deepseek": 1, "tongyi": 1}}
    )
    out2 = compute_mention_metrics(spec=tightened, answers=_answers(), citations=[])
    # deepseek 问题一 cell 丢 2 + tongyi 问题一/背景问题 cell 各丢 1；w1 只剩 3 条
    assert out2["dropped_extra_answers"] == 4
    assert out2["w1_total"] == 3


def test_unknown_platform_samples_fail_loud() -> None:
    spec = _spec()
    broken = MentionMetricsSpec(**{**spec.__dict__, "samples_per_query": {"deepseek": 1}})
    with pytest.raises(ValueError, match="tongyi"):
        compute_mention_metrics(spec=broken, answers=_answers(), citations=[])


def test_name_hits_and_occ() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    deepseek = out["w1_platform"]["deepseek"]
    assert deepseek["n"] == 1 and deepseek["uq"] == 1
    # 命中=精确子串；occ=原文总次数（ds resp 两处咱家果源）
    assert deepseek["咱家果源"] == {"answers": 1, "occ": 2}
    assert deepseek["汇源"] == {"answers": 1, "occ": 1}
    tongyi = out["w1_platform"]["tongyi"]
    assert tongyi["咱家果源"] == {"answers": 1, "occ": 1}
    assert tongyi["汇源"] == {"answers": 1, "occ": 2}
    assert out["w1_name_totals"] == {"咱家果源": 2, "汇源": 2}


def test_scene_name_hits() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    assert out["w1_group"] == {"ZJ-Q003|NFC果汁候选集": {"_n": 4, "咱家果源": 2, "汇源": 2}}


def test_brand_board() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    assert out["w1_brands"] == {"汇源": 2, "咱家果源": 2}
    assert out["w1_brands_by_platform"]["汇源"] == {"deepseek": 1, "tongyi": 1}
    assert out["w1_brands_by_platform"]["咱家果源"] == {"deepseek": 1, "tongyi": 1}


def test_mentions_context_window_and_flags() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    mentions = out["w1_mentions"]
    assert [m["pub"] for m in mentions] == ["ans_ds", "ans_ty1"]
    ds = mentions[0]
    assert ds["model"] == "deepseek"
    assert ds["group"] == "ZJ-Q003" and ds["scene"] == "NFC果汁候选集"
    assert ds["variant"] == 1
    assert ds["has_huiyuan"] is True
    # 上下文窗口=命中点前 150 / 后 200（命中点在 200）
    resp = "x" * 200 + "咱家果源是汇源系新品牌，咱家果源主打NFC。" + "y" * 300
    assert ds["context"] == resp[50:400]
    assert mentions[1]["has_huiyuan"] is False


def test_citation_aggregation_with_none_host_url() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=_citations())
    # 丢弃样本与 W2 答案的 citation 不进统计；distinct url=3（example a/b + other x）
    assert out["w1_citations_total"] == {"records": 5, "domains": 2, "urls": 3}
    by_platform = out["w1_citations_by_platform"]
    assert by_platform["deepseek"] == {"records": 2, "domains": 1, "urls": 2}
    assert by_platform["tongyi"] == {"records": 3, "domains": 2, "urls": 2}
    assert out["w1_top_hosts"] == [["example.com", 3], ["other.com", 1]]
    assert out["w1_top_hosts_by_platform"]["deepseek"] == [["example.com", 2]]
    assert out["w1_top_hosts_by_platform"]["tongyi"] == [
        ["example.com", 1],
        ["other.com", 1],
    ]


def test_brand_pages_tracking() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=_citations())
    pages = {tuple(page[k] for k in ("host", "url")): page for page in out["w1_brand_pages"]}
    assert set(pages) == {("example.com", "https://example.com/a"), (None, None)}
    own = pages[("example.com", "https://example.com/a")]
    assert own["title"] == "咱家果源官网"
    assert own["n_answers"] == 1 and own["n_mentioned"] == 1
    assert own["models"] == ["deepseek"]
    anon = pages[(None, None)]
    assert anon["n_answers"] == 1 and anon["n_mentioned"] == 1
    assert anon["models"] == ["tongyi"]
    assert out["w1_brand_pages_distinct_answers"] == 2
    assert out["w1_brand_pages_mentioned_answers"] == 2


def test_w2_coverage_terms_and_excerpts() -> None:
    out = compute_mention_metrics(spec=_spec(), answers=_answers(), citations=[])
    assert out["w2_coverage"] == {"ZJ-Q001": {"deepseek": 1, "tongyi": 2}}
    assert out["w2_terms"] == {"NFC": 1, "无法核实/资料不足": 1}
    excerpts = out["w2_q001_q002"]
    assert [(e["group"], e["model"]) for e in excerpts] == [
        ("ZJ-Q001", "deepseek"),
        ("ZJ-Q001", "tongyi"),
        ("ZJ-Q001", "tongyi"),
    ]
    assert excerpts[0]["excerpt"] == "NFC是浓缩还原之外路线。"


def test_empty_result_honest_structure() -> None:
    """零覆盖不报错：全零/空结构如实返回（w2 组键仍在、计数为空）。"""
    out = compute_mention_metrics(spec=_spec(), answers=[], citations=[])
    assert out["w1_total"] == 0 and out["w2_total"] == 0
    assert out["dropped_extra_answers"] == 0
    for platform in ("deepseek", "tongyi"):
        assert out["platform_coverage"][platform] == {
            "w1_answers": 0,
            "w1_queries": 0,
            "w2_answers": 0,
            "w2_queries": 0,
        }
        assert out["w1_platform"][platform]["n"] == 0
        assert out["w1_platform"][platform]["咱家果源"] == {"answers": 0, "occ": 0}
        assert out["w1_top_hosts_by_platform"][platform] == []
    assert out["w1_name_totals"] == {"咱家果源": 0, "汇源": 0}
    assert out["w1_group"] == {} and out["w1_mentions"] == []
    assert out["w1_brands"] == {"汇源": 0, "咱家果源": 0}
    assert out["w1_citations_by_platform"] == {}
    assert out["w1_citations_total"] == {"records": 0, "domains": 0, "urls": 0}
    assert out["w1_top_hosts"] == [] and out["w1_brand_pages"] == []
    assert out["w1_brand_pages_distinct_answers"] == 0
    assert out["w1_brand_pages_mentioned_answers"] == 0
    assert out["w2_coverage"] == {"ZJ-Q001": {}}
    assert out["w2_terms"] == {"NFC": 0, "无法核实/资料不足": 0}
    assert out["w2_q001_q002"] == []


def test_spec_hash_stable_and_sensitive() -> None:
    spec = _spec()
    # samples_per_query 键序不影响 hash（canonical json sort_keys）
    reordered = MentionMetricsSpec(
        **{**spec.__dict__, "samples_per_query": {"tongyi": 2, "deepseek": 1}}
    )
    assert spec_hash(reordered) == spec_hash(spec)
    # 与手工 canonical json 一致
    canonical = json.dumps(
        spec_payload(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    import hashlib

    assert spec_hash(spec) == hashlib.sha256(canonical.encode()).hexdigest()
    changed = MentionMetricsSpec(**{**spec.__dict__, "terms": ("NFC", "商标")})
    assert spec_hash(changed) != spec_hash(spec)


def test_wave_spec_from_truth_json() -> None:
    payload = {
        "query_groups": [
            {
                "name": "ZJ-Q003｜品类发现｜NFC果汁候选集",
                "items": [{"text": "问题一", "priority": 1}],
            }
        ]
    }
    wave = wave_spec_from_truth("w1", payload)
    assert wave.wave == "w1"
    assert wave.query_groups[0].name == "ZJ-Q003｜品类发现｜NFC果汁候选集"
    assert wave.query_groups[0].items[0] == QueryItem(text="问题一", priority=1)


# ── POST /api/v2/analytics/mention-metrics 端点门 ────────────────────────────
def _override_principal(role: Role = Role.OPERATOR, tenant: str = TENANT) -> None:
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u-mention", role=role, tenant_pub_id=tenant
    )


@pytest.fixture(autouse=True)
def _clean_overrides() -> Any:
    yield
    app.dependency_overrides.pop(get_principal, None)


def _body() -> dict[str, Any]:
    return {
        "project_pub_id": PROJECT,
        "spec": {
            "platforms": ["deepseek", "tongyi"],
            "samples_per_query": {"deepseek": 1, "tongyi": 2},
            "names": ["咱家果源", "汇源"],
            "brands": ["汇源", "咱家果源"],
            "terms": ["NFC"],
            "primary_name": "咱家果源",
            "waves": [
                {
                    "wave": "w1",
                    "query_groups": [
                        {
                            "name": "ZJ-Q003｜品类发现｜NFC果汁候选集",
                            "items": [{"text": "问题一", "priority": 1}],
                        }
                    ],
                },
                {
                    "wave": "w2",
                    "query_groups": [
                        {
                            "name": "ZJ-Q001｜品牌背景｜股权",
                            "items": [{"text": "背景问题", "priority": 1}],
                        }
                    ],
                },
            ],
            "mention_flags": [{"name": "汇源", "key": "has_huiyuan"}],
            "excerpt_groups": ["ZJ-Q001"],
        },
    }


def _patch_compute(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any] | Exception
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_compute(dsn: str, tenant_pub_id: str, *, project_pub_id: str, spec: Any):
        seen["tenant_pub_id"] = tenant_pub_id
        seen["project_pub_id"] = project_pub_id
        seen["spec"] = spec
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(mention_metrics_service, "compute_for_project", fake_compute)
    return seen


def test_api_requires_authentication_401() -> None:
    assert client.post("/api/v2/analytics/mention-metrics", json=_body()).status_code == 401


def test_api_permission_denied_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal(Role.WORKER)
    monkeypatch.setattr(
        mention_metrics_service,
        "compute_for_project",
        lambda *a, **k: pytest.fail("越权访问不应触达服务层"),
    )
    resp = client.post("/api/v2/analytics/mention-metrics", json=_body())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"


def test_api_project_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    seen = _patch_compute(monkeypatch, mention_metrics_service.ProjectNotFound("x"))
    resp = client.post("/api/v2/analytics/mention-metrics", json=_body())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
    # 租户隔离：传给服务层的是当前 principal 的租户
    assert seen["tenant_pub_id"] == TENANT and seen["project_pub_id"] == PROJECT


def test_api_happy_path_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    sentinel = {"metric_version": MENTION_METRICS_VERSION, "spec_hash": "x" * 64}
    seen = _patch_compute(monkeypatch, sentinel)
    resp = client.post("/api/v2/analytics/mention-metrics", json=_body())
    assert resp.status_code == 200
    assert resp.json() == sentinel
    spec = seen["spec"]
    assert isinstance(spec, MentionMetricsSpec)
    assert spec.platforms == ("deepseek", "tongyi")
    assert spec.samples_per_query == {"deepseek": 1, "tongyi": 2}
    assert spec.primary_name == "咱家果源"
    assert [wave.wave for wave in spec.waves] == ["w1", "w2"]
    assert spec.mention_flags == (MentionFlag(name="汇源", key="has_huiyuan"),)


def test_api_invalid_spec_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _override_principal()
    monkeypatch.setattr(
        mention_metrics_service,
        "compute_for_project",
        lambda *a, **k: pytest.fail("spec 不合法不应触达服务层"),
    )
    # waves 缺 w2（重复 w1）
    body = _body()
    body["spec"]["waves"][1]["wave"] = "w1"
    resp = client.post("/api/v2/analytics/mention-metrics", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_mention_metrics_spec"
    # samples_per_query 未覆盖 platforms
    body = _body()
    del body["spec"]["samples_per_query"]["tongyi"]
    resp = client.post("/api/v2/analytics/mention-metrics", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_mention_metrics_spec"
    # 未知字段（StrictModel extra=forbid）→ pydantic 422
    body = _body()
    body["spec"]["unexpected"] = 1
    assert client.post("/api/v2/analytics/mention-metrics", json=body).status_code == 422
