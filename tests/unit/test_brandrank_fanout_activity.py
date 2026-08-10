"""workflows/activities/s02.py extract_brands_activity 单元测试（W3 fanout 落账）。

全 fake：不连 PG（monkeypatch _record_brand_extract + fetch_project_brandrank_domain
两接缝）、不烧 LLM（monkeypatch extract.default_client 注入 SDK 形状假 client）。
覆盖：幂等重抽键、LLM 失败/未配置诚实落账、异常类名不落值、domain 未设置
fail-loud 落标记、非 fanout 载荷 skipped。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from geo_platform.brandrank import service as brandrank_service

from domain.brandrank import extract
from workflows.activities import s02 as s02_activities

PAYLOAD: dict[str, Any] = {
    "persist": True,
    "tenant_pub_id": "tnt_fanout",
    "project_pub_id": "prj_fanout",
    "answer_pub_id": "ans_fanout_1",
    "text": "推荐中意人寿，中国平安次之。",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每用例隔离：LLM env 清空（各用例按需自行 setenv）。"""
    for key in (
        "GEO_BRANDRANK_LLM_API_KEY",
        "GEO_BRANDRANK_LLM_BASE_URL",
        "GEO_BRANDRANK_LLM_BASE_URL_FALLBACK",
        "GEO_BRANDRANK_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """拦截 _record_brand_extract 落账；返回已落行列表（按调用序）。"""
    rows: list[dict[str, Any]] = []

    def fake_record(dsn: str, **kwargs: Any) -> None:
        rows.append({"dsn": dsn, **kwargs})

    monkeypatch.setattr(s02_activities, "_record_brand_extract", fake_record)
    return rows


@pytest.fixture
def project_domain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """拦截项目 domain 真源查询；缺省返回 legal（写入 box 可改）。"""
    box: dict[str, str | None] = {"domain": "legal"}
    monkeypatch.setattr(
        brandrank_service,
        "fetch_project_brandrank_domain",
        lambda dsn, tenant, project: box["domain"],
    )
    # activities/s02.py 是直接 import 的函数引用，需同步 patch 模块内名字
    monkeypatch.setattr(
        s02_activities,
        "fetch_project_brandrank_domain",
        lambda dsn, tenant, project: box["domain"],
    )
    return box


class FakeClient:
    """OpenAI SDK 形状假 client：按 behavior 返品牌列表或抛错。"""

    def __init__(self, behavior):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._behavior = behavior

    def _create(self, *, model, messages, temperature, response_format):
        self.calls += 1
        outcome = self._behavior()
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"brands": outcome}, ensure_ascii=False)
                    )
                )
            ]
        )


async def test_happy_path_records_ok_row(
    recorded, project_domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    monkeypatch.setenv("GEO_BRANDRANK_LLM_MODEL", "m-test")
    fake = FakeClient(lambda: ["中意人寿", "中国平安"])
    monkeypatch.setattr(extract, "default_client", lambda: fake)

    result = await s02_activities.extract_brands_activity(PAYLOAD)

    assert result["state"] == "ok" and result["domain"] == "legal"
    assert result["model"] == "m-test" and result["brand_count"] == 2
    assert fake.calls == 1
    assert len(recorded) == 1
    row = recorded[0]
    assert row["tenant_pub_id"] == "tnt_fanout"
    assert row["answer_pub_id"] == "ans_fanout_1"
    assert row["domain"] == "legal"
    assert row["brands"] == ["中意人寿", "中国平安"]
    assert row["status"] == "ok" and row["model"] == "m-test" and row["error"] is None


async def test_idempotent_reextract_same_key(
    recorded, project_domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重放/重试同 (tenant,answer,domain) 键重复落账——幂等由 SQL ON CONFLICT 保证，
    这里钉死两次调用的落账键完全一致（集成测试对真库验证单行覆盖）。"""
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    fake = FakeClient(lambda: ["中意人寿"])
    monkeypatch.setattr(extract, "default_client", lambda: fake)

    first = await s02_activities.extract_brands_activity(PAYLOAD)
    second = await s02_activities.extract_brands_activity(dict(PAYLOAD))

    assert first["state"] == second["state"] == "ok"
    assert len(recorded) == 2

    def key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (row["tenant_pub_id"], row["answer_pub_id"], row["domain"])

    assert key(recorded[0]) == key(recorded[1])


async def test_llm_failure_records_failed_row_without_exception_class(
    recorded, project_domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 单条失败：落 failed+消毒后错误类别（异常类名不落值），activity 不抛。"""
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    monkeypatch.setenv("GEO_BRANDRANK_LLM_MODEL", "m-test")

    def boom() -> Exception:
        return extract.ExtractError("api_error: ReadTimeout: timed out after 60s")

    fake = FakeClient(boom)
    monkeypatch.setattr(extract, "default_client", lambda: fake)

    result = await s02_activities.extract_brands_activity(PAYLOAD)

    assert result["state"] == "failed"
    assert len(recorded) == 1
    row = recorded[0]
    assert row["status"] == "failed" and row["model"] == "m-test"
    assert row["brands"] == []  # 绝不把失败伪装成空列表命中
    assert row["error"].startswith("api_error:")
    assert "ReadTimeout" not in row["error"]  # 异常类名不落值
    assert "<exc>" in row["error"]


async def test_llm_disabled_records_failed_row(recorded, project_domain) -> None:
    """未配 GEO_BRANDRANK_LLM_API_KEY：落 failed/llm_disabled，绝不合成。"""
    result = await s02_activities.extract_brands_activity(PAYLOAD)
    assert result == {"state": "failed", "error": "llm_disabled", "domain": "legal"}
    assert len(recorded) == 1
    assert recorded[0]["status"] == "failed"
    assert recorded[0]["error"] == "llm_disabled"
    assert recorded[0]["domain"] == "legal"


async def test_domain_unset_records_marker_without_llm(
    recorded, project_domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """项目未设 brandrank_domain 真源：跳过抽取并落 failed/domain_unset 标记行
    （domain 列 '' 占位保唯一键幂等），LLM client 构造被调即失败。"""
    project_domain["domain"] = None
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")  # 有 key 也不该烧
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("domain 未设不应调 LLM"))

    result = await s02_activities.extract_brands_activity(PAYLOAD)

    assert result == {"state": "failed", "error": "domain_unset", "domain": ""}
    assert len(recorded) == 1
    assert recorded[0]["status"] == "failed"
    assert recorded[0]["error"] == "domain_unset"
    assert recorded[0]["domain"] == ""


async def test_unknown_domain_column_value_fail_loud(
    recorded, project_domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真源列值非法（绕过 API 校验的直写）：failed/unknown_domain，绝不臆造规则包。"""
    project_domain["domain"] = "不存在的领域"
    monkeypatch.setenv("GEO_BRANDRANK_LLM_API_KEY", "k-test")
    monkeypatch.setattr(extract, "default_client", lambda: pytest.fail("未知 domain 不应调 LLM"))

    result = await s02_activities.extract_brands_activity(PAYLOAD)

    assert result == {"state": "failed", "error": "unknown_domain", "domain": "不存在的领域"}
    assert recorded[0]["status"] == "failed"


async def test_non_fanout_payload_skipped(recorded) -> None:
    """非持久化/缺上下文载荷（直接起 workflow 的调试载荷）：skipped，零落账零 LLM。"""
    for payload in (
        {**PAYLOAD, "persist": False},
        {k: v for k, v in PAYLOAD.items() if k != "persist"},
        {**PAYLOAD, "tenant_pub_id": ""},
        {**PAYLOAD, "answer_pub_id": ""},
    ):
        result = await s02_activities.extract_brands_activity(payload)
        assert result == {"state": "skipped", "reason": "missing_context"}
    assert recorded == []


def test_sanitize_extract_error_strips_exception_classes() -> None:
    sanitize = s02_activities._sanitize_extract_error
    assert sanitize("api_error: ReadTimeout: timed out") == "api_error: <exc>: timed out"
    assert sanitize("unexpected: ConnectError: refused") == "unexpected: <exc>: refused"
    assert sanitize("bad_json: Expecting value") == "bad_json: Expecting value"
    assert len(sanitize("x" * 1000)) == 500
