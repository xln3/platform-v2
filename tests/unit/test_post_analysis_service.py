"""post_analysis service 单测（fake psycopg 连接，不打真 DB/MinIO）。

覆盖：创建幂等（同 key 同体重放 / 同 key 异体 409 / 无 key 按请求体指纹）、
URL 校验（非 http/https 拒绝、去重、上限）、任务/条目游标分页、
任务状态计数、列表行 analysis 派生字段。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

import psycopg
import pytest
from geo_platform.post_analysis.service import (
    PostAnalysisConflict,
    PostAnalysisInvalid,
    PostAnalysisService,
    derive_item_pub_id,
    derive_task_pub_id,
    request_fingerprint,
    validate_urls,
)

_TENANT = "tnt_0123456789abcdef"
_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    """可编程 fake：按 SQL 片段路由到内存态（tasks/items/commands）。"""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}  # pub_id → task 行
        self.items: list[dict[str, Any]] = []
        self.commands: list[tuple[Any, ...]] = []
        self.assets: dict[str, dict[str, Any]] = {}  # object_key → asset 行
        self.commits = 0

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: Any) -> Literal[False]:
        return False

    def commit(self) -> None:
        self.commits += 1

    def seed_task(self, pub_id: str, **overrides: Any) -> dict[str, Any]:
        row = {
            "id": f"uuid-{pub_id}",
            "pub_id": pub_id,
            "tenant_id": _TENANT_ID,
            "target_brand": "中意人寿",
            "target_brand_aliases": [],
            "status": "queued",
            "url_count": 1,
            "options": {"verify_facts": True, "annotate": True},
            "idempotency_key": None,
            "workflow_id": f"post-analysis/{_TENANT}/{pub_id}",
            "error": None,
            "created_by": "usr_x",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        row.update(overrides)
        self.tasks[pub_id] = row
        return row

    def seed_item(self, task_row: dict[str, Any], pub_id: str, **overrides: Any) -> None:
        row = {
            "id": f"uuid-{pub_id}",
            "pub_id": pub_id,
            "task_id": task_row["id"],
            "tenant_id": _TENANT_ID,
            "ordinal": len(self.items),
            "url": f"https://a.example.com/{pub_id}",
            "url_hash": "h" * 64,
            "host": "a.example.com",
            "status": "completed",
            "annotation_status": "completed",
            "final_url": None,
            "http_status": None,
            "extractor": None,
            "text_cas_key": None,
            "text_sha256": None,
            "screenshot_cas_key": None,
            "annotated_cas_key": None,
            "analysis": None,
            "analysis_validation": None,
            "annotations": None,
            "error": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        row.update(overrides)
        self.items.append(row)

    def seed_asset(
        self, object_key: str, *, sha256: str, byte_size: int, mime_type: str = "image/png"
    ) -> None:
        self.assets[object_key] = {
            "sha256": sha256,
            "byte_size": byte_size,
            "mime_type": mime_type,
        }

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Result:
        params = params or ()
        if "FROM platform.tenant" in sql:
            return _Result([{"id": _TENANT_ID}])
        if "set_config" in sql:
            return _Result([])
        if "FROM evidence.evidence_asset" in sql:
            asset = self.assets.get(str(params[1]))
            return _Result([asset] if asset else [])
        if "INSERT INTO platform.post_analysis_task" in sql:
            pub_id = str(params[0])
            if pub_id not in self.tasks:
                self.seed_task(
                    pub_id,
                    target_brand=params[2],
                    target_brand_aliases=json.loads(str(params[3])),
                    url_count=int(params[4]),
                    options=json.loads(str(params[5])),
                    idempotency_key=params[6],
                    workflow_id=str(params[7]),
                    created_by=str(params[8]),
                )
            return _Result([])
        if "INSERT INTO platform.post_analysis_item" in sql:
            task_row = next(t for t in self.tasks.values() if t["id"] == str(params[1]))
            self.seed_item(
                task_row,
                str(params[0]),
                ordinal=int(params[3]),
                url=str(params[4]),
                url_hash=str(params[5]),
                host=str(params[6]),
                status="pending",
                annotation_status="pending",
            )
            return _Result([])
        if "INSERT INTO integration.workflow_start_command" in sql:
            self.commands.append(params)
            return _Result([])
        if "UPDATE platform.post_analysis_task" in sql and "options" in sql:
            row = self.tasks[str(params[1])]
            row["options"] = {**row["options"], **json.loads(str(params[0]))}
            return _Result([])
        if "FROM platform.post_analysis_item" in sql and "GROUP BY status" in sql:
            counts: dict[str, int] = {}
            for item in self.items:
                if item["task_id"] == str(params[0]):
                    counts[item["status"]] = counts.get(item["status"], 0) + 1
            return _Result([{"status": s, "n": n} for s, n in counts.items()])
        if "FROM platform.post_analysis_item" in sql and "WHERE pub_id" in sql:
            rows = [i for i in self.items if i["pub_id"] == str(params[0])]
            return _Result(rows)
        if "FROM platform.post_analysis_item" in sql:
            task_id, cursor, _cursor2, limit = (
                str(params[0]),
                params[1],
                params[2],
                int(params[3]),
            )
            rows = [i for i in self.items if i["task_id"] == task_id]
            rows.sort(key=lambda i: i["pub_id"])
            if cursor is not None:
                rows = [i for i in rows if i["pub_id"] > str(cursor)]
            return _Result(rows[:limit])
        if "FROM platform.post_analysis_task" in sql and "WHERE pub_id" in sql:
            found = self.tasks.get(str(params[0]))
            return _Result([found] if found else [])
        if "FROM platform.post_analysis_task" in sql and "WHERE id" in sql:
            rows = [t for t in self.tasks.values() if t["id"] == str(params[0])]
            return _Result(rows)
        if "FROM platform.post_analysis_task" in sql:
            cursor, limit = params[1], int(params[3])
            rows = sorted(self.tasks.values(), key=lambda t: t["pub_id"])
            if cursor is not None:
                rows = [t for t in rows if t["pub_id"] > str(cursor)]
            return _Result(rows[:limit])
        return _Result([])


def _service(fake: _FakeConnection) -> PostAnalysisService:
    factory = cast(Callable[[], psycopg.Connection[Any]], lambda: fake)
    return PostAnalysisService(dsn="fake", connect=factory)


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "tenant_pub_id": _TENANT,
        "created_by_pub_id": "usr_x",
        "target_brand": "中意人寿",
        "target_brand_aliases": ["Generali China"],
        "urls": ["https://a.example.com/1", "https://b.example.com/2"],
        "options": {"verify_facts": True, "annotate": True},
        "idempotency_key": None,
        "task_queue": "geo-platform-v2",
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# URL 校验
# ---------------------------------------------------------------------------


def test_validate_urls_rejects_non_http() -> None:
    with pytest.raises(PostAnalysisInvalid):
        validate_urls(["ftp://a.example.com/x"], max_urls=50)
    with pytest.raises(PostAnalysisInvalid):
        validate_urls(["not-a-url"], max_urls=50)
    with pytest.raises(PostAnalysisInvalid):
        validate_urls([], max_urls=50)


def test_validate_urls_dedupes_normalized() -> None:
    targets = validate_urls(
        [
            "https://www.a.example.com/post/#frag",
            "https://a.example.com/post",
            "https://b.example.com/x",
        ],
        max_urls=50,
    )
    assert [t.host for t in targets] == ["a.example.com", "b.example.com"]
    assert [t.ordinal for t in targets] == [0, 1]
    assert len({t.url_hash for t in targets}) == 2


def test_validate_urls_enforces_cap() -> None:
    urls = [f"https://{i}.example.com/a" for i in range(51)]
    with pytest.raises(PostAnalysisInvalid, match="超上限"):
        validate_urls(urls, max_urls=50)
    assert len(validate_urls(urls[:50], max_urls=50)) == 50


# ---------------------------------------------------------------------------
# 创建幂等
# ---------------------------------------------------------------------------


def test_create_task_inserts_items_and_outbox_command() -> None:
    fake = _FakeConnection()
    row, created = _service(fake).create_task(**_create_kwargs())
    assert created is True
    assert row["pub_id"].startswith("pat_") and len(row["pub_id"]) == 30
    assert row["status"] == "queued" and row["url_count"] == 2
    # 两条 item：确定性 pub_id + 归一化 host
    assert len(fake.items) == 2
    for item in fake.items:
        assert item["pub_id"] == derive_item_pub_id(
            _TENANT, row["pub_id"], item["url_hash"]
        )
        assert item["status"] == "pending"
    # 同事务 outbox 命令：workflow_type=post_analysis，payload 指回 task
    assert len(fake.commands) == 1
    command = fake.commands[0]
    assert command[2] == "post_analysis"
    assert command[3] == f"post-analysis/{_TENANT}/{row['pub_id']}"
    payload = json.loads(str(command[5]))
    assert payload == {"tenant_pub_id": _TENANT, "task_pub_id": row["pub_id"]}
    assert fake.commits == 1


def test_create_task_same_key_same_body_replays() -> None:
    fake = _FakeConnection()
    key = "k" * 16
    first, created1 = _service(fake).create_task(**_create_kwargs(idempotency_key=key))
    second, created2 = _service(fake).create_task(**_create_kwargs(idempotency_key=key))
    assert created1 is True and created2 is False
    assert second["pub_id"] == first["pub_id"]
    assert len(fake.items) == 2  # 没有重复插 item
    assert len(fake.commands) == 1  # 没有重复发 workflow


def test_create_task_same_key_different_body_conflicts() -> None:
    fake = _FakeConnection()
    key = "k" * 16
    _service(fake).create_task(**_create_kwargs(idempotency_key=key))
    with pytest.raises(PostAnalysisConflict):
        _service(fake).create_task(
            **_create_kwargs(idempotency_key=key, target_brand="别的品牌")
        )


def test_create_task_without_key_uses_body_fingerprint() -> None:
    fake = _FakeConnection()
    first, _ = _service(fake).create_task(**_create_kwargs())
    # 无 key 时相同提交天然幂等重放
    second, created = _service(fake).create_task(**_create_kwargs())
    assert created is False and second["pub_id"] == first["pub_id"]
    expected = derive_task_pub_id(
        _TENANT,
        None,
        request_fingerprint(
            target_brand="中意人寿",
            target_brand_aliases=["Generali China"],
            urls=validate_urls(_create_kwargs()["urls"], max_urls=50),
            options={"verify_facts": True, "annotate": True, "open_investigation": True},
        ),
    )
    assert first["pub_id"] == expected


# ---------------------------------------------------------------------------
# 分页 / 详情
# ---------------------------------------------------------------------------


def test_list_tasks_cursor_pagination() -> None:
    fake = _FakeConnection()
    for index in range(3):
        fake.seed_task(f"pat_{index:026d}")
    service = _service(fake)
    first_page = service.list_tasks(tenant_pub_id=_TENANT, cursor=None, limit=2)
    # service 取 limit+1 行（router 侧据此判 has_more、截 limit 行）
    assert len(first_page) == 3
    cursor = first_page[0]["pub_id"]
    second_page = service.list_tasks(tenant_pub_id=_TENANT, cursor=cursor, limit=50)
    assert second_page  # 游标之后仍有数据
    assert all(row["pub_id"] > cursor for row in second_page)
    assert [row["pub_id"] for row in second_page] == [
        row["pub_id"] for row in first_page if row["pub_id"] > cursor
    ]


def test_get_task_includes_status_counts() -> None:
    fake = _FakeConnection()
    task = fake.seed_task("pat_" + "0" * 26)
    fake.seed_item(task, "pai_a", status="completed")
    fake.seed_item(task, "pai_b", status="fetch_failed")
    fake.seed_item(task, "pai_c", status="fetch_failed")
    row = _service(fake).get_task(tenant_pub_id=_TENANT, task_pub_id=task["pub_id"])
    assert row["status_counts"] == {"completed": 1, "fetch_failed": 2}


def test_list_items_derives_analysis_fields() -> None:
    fake = _FakeConnection()
    task = fake.seed_task("pat_" + "0" * 26)
    fake.seed_item(
        task,
        "pai_a",
        analysis={
            "category": "review_ranking",
            "category_label": "评测榜单",
            "is_geo_post": True,
            "is_target_brand_geo": True,
            "disparagement": [{"quote": "q"}],
            "claims": [
                {"verification": {"verdict": "inaccurate"}},
                {"verification": {"verdict": "accurate"}},
                {"verification": None},
            ],
        },
    )
    fake.seed_item(task, "pai_b", analysis=None)
    rows = _service(fake).list_items(
        tenant_pub_id=_TENANT, task_pub_id=task["pub_id"], cursor=None, limit=50
    )
    assert len(rows) == 2
    first = next(row for row in rows if row["pub_id"] == "pai_a")
    assert first["category"] == "review_ranking"
    assert first["is_geo_post"] is True
    assert first["disparagement_count"] == 1
    assert first["misinformation_count"] == 1
    second = next(row for row in rows if row["pub_id"] == "pai_b")
    assert second["category"] is None and second["misinformation_count"] == 0


# ---------------------------------------------------------------------------
# item detail 资产完整性三元组（verified-Blob 边界）
# ---------------------------------------------------------------------------


def test_get_item_detail_exposes_asset_integrity_triples() -> None:
    fake = _FakeConnection()
    task = fake.seed_task("pat_" + "0" * 26)
    fake.seed_item(
        task,
        "pai_a",
        screenshot_cas_key="cas/png/1",
        annotated_cas_key="cas/png/2",
    )
    fake.seed_asset("cas/png/1", sha256="a" * 64, byte_size=1234)
    fake.seed_asset("cas/png/2", sha256="b" * 64, byte_size=5678)
    row = _service(fake).get_item(tenant_pub_id=_TENANT, item_pub_id="pai_a")
    assert row["has_screenshot"] is True and row["has_annotated"] is True
    assert row["screenshot_asset"] == {
        "sha256": "a" * 64,
        "byte_size": 1234,
        "mime_type": "image/png",
    }
    assert row["annotated_asset"] == {
        "sha256": "b" * 64,
        "byte_size": 5678,
        "mime_type": "image/png",
    }
    # cas key 不外泄
    assert "screenshot_cas_key" not in row and "annotated_cas_key" not in row


def test_get_item_detail_absent_assets_are_null() -> None:
    fake = _FakeConnection()
    task = fake.seed_task("pat_" + "0" * 26)
    fake.seed_item(task, "pai_a")
    row = _service(fake).get_item(tenant_pub_id=_TENANT, item_pub_id="pai_a")
    assert row["has_screenshot"] is False and row["has_annotated"] is False
    assert row["screenshot_asset"] is None and row["annotated_asset"] is None


def test_get_item_detail_dangling_cas_key_degrades_to_null() -> None:
    fake = _FakeConnection()
    task = fake.seed_task("pat_" + "0" * 26)
    # cas 引用悬空（evidence_asset 无对应行）→ 如实降级为 null，绝不编造三元组
    fake.seed_item(task, "pai_a", screenshot_cas_key="cas/png/missing")
    row = _service(fake).get_item(tenant_pub_id=_TENANT, item_pub_id="pai_a")
    assert row["has_screenshot"] is True  # cas 引用在 → 布尔仍真
    assert row["screenshot_asset"] is None


def test_get_task_detail_exposes_investigation_pub_id() -> None:
    fake = _FakeConnection()
    fake.seed_task(
        "pat_" + "0" * 26,
        options={
            "verify_facts": True,
            "annotate": True,
            "open_investigation": True,
            "investigation_pub_id": "inv_abc123",
        },
    )
    row = _service(fake).get_task(tenant_pub_id=_TENANT, task_pub_id="pat_" + "0" * 26)
    assert row["investigation_pub_id"] == "inv_abc123"


def test_get_task_detail_investigation_pub_id_defaults_null() -> None:
    fake = _FakeConnection()
    fake.seed_task("pat_" + "0" * 26)
    row = _service(fake).get_task(tenant_pub_id=_TENANT, task_pub_id="pat_" + "0" * 26)
    assert row["investigation_pub_id"] is None


def test_create_task_normalizes_open_investigation_option() -> None:
    fake = _FakeConnection()
    row, created = _service(fake).create_task(
        **_create_kwargs(options={"verify_facts": True, "annotate": True,
                                  "open_investigation": False})
    )
    assert created is True
    stored = fake.tasks[row["pub_id"]]
    assert stored["options"]["open_investigation"] is False
