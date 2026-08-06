from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.datasets import router as datasets_router
from geo_platform.main import app


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    tenant_pub_id = response.json()["tenant_pub_id"]
    return tenant_pub_id, {
        "X-Tenant-Id": tenant_pub_id,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }


def _member_headers(
    client: TestClient,
    admin_headers: dict[str, str],
    subject: str,
    role: str,
) -> dict[str, str]:
    response = client.post(
        "/api/v2/identity/members",
        headers=admin_headers,
        json={"subject": subject, "display_name": subject, "role": role},
    )
    assert response.status_code == 201
    return {
        "X-Tenant-Id": admin_headers["X-Tenant-Id"],
        "X-Actor-Id": subject,
        "X-Actor-Role": role,
    }


@pytest.fixture
def datasets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))
    get_settings.cache_clear()
    datasets_router._cache.clear()
    yield tmp_path
    datasets_router._cache.clear()
    get_settings.cache_clear()


def _write_dataset(directory: Path) -> str:
    payload = {
        "generated_at": "2026-07-27 15:53",
        "sources": {"prfabu": "prfabu媒体管家", "toumeiw": "投媒网", "mtpfw": "媒体批发网"},
        "partial": {"toumeiw": True},
        "stats": {
            "counts": {"prfabu": 1, "toumeiw": 1, "mtpfw": 0},
            "geo_counts": {"prfabu": 1, "toumeiw": 0, "mtpfw": 0},
            "unique_media": 1,
            "matched_2plus": 0,
            "matched_3": 0,
            "geo_union": 1,
            "geo_multi_src": 0,
        },
        "rows": [
            {
                "name": "示例媒体",
                "prices": {"prfabu": 100, "toumeiw": 80},
                "best": 80,
                "best_plat": "toumeiw",
                "spread": 1.3,
                "n_src": 2,
                "geo": ["b"],
                "geo_n": 1,
                "ids": {"prfabu": 1},
            }
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (directory / "media-prices.json").write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    (directory / "media-prices.sha256").write_text(
        f"{digest}  media-prices.json\n", encoding="utf-8"
    )
    return digest


def _write_wemedia_dataset(directory: Path) -> str:
    payload = {
        "generated_at": "2026-07-28 18:30",
        "sources": {"prfabu": "prfabu媒体管家"},
        "partial": {"prfabu": False},
        "stats": {
            "counts": {"prfabu": 1},
            "geo_counts": {"prfabu": 0},
            "unique_media": 1,
            "matched_2plus": 0,
            "matched_3": 0,
            "geo_union": 0,
            "geo_multi_src": 0,
        },
        "rows": [
            {
                "name": "示例账号",
                "platform": "百家号",
                "prices": {"prfabu": 88},
                "best": 88,
                "best_plat": "prfabu",
                "spread": None,
                "n_src": 1,
                "geo": [],
                "geo_n": 0,
            }
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (directory / "media-wemedia.json").write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    (directory / "media-wemedia.sha256").write_text(
        f"{digest}  media-wemedia.json\n", encoding="utf-8"
    )
    return digest


def test_media_prices_dataset_serves_operator_with_integrity_headers(datasets_dir: Path) -> None:
    digest = _write_dataset(datasets_dir)
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    operator_headers = _member_headers(
        client, admin_headers, f"datasets-operator-{suffix}", "operator"
    )
    response = client.get("/api/v2/datasets/media-prices", headers=operator_headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-dataset-sha256"] == digest
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    payload = response.json()
    assert payload["stats"]["unique_media"] == 1
    assert payload["rows"][0]["name"] == "示例媒体"
    assert hashlib.sha256(response.content).hexdigest() == digest


def test_media_prices_dataset_cache_is_invalidated_when_sidecar_is_replaced(
    datasets_dir: Path,
) -> None:
    old_digest = _write_dataset(datasets_dir)
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"datasets-sidecar-{suffix}")

    initial = client.get("/api/v2/datasets/media-prices", headers=admin_headers)
    assert initial.status_code == 200
    assert initial.headers["x-dataset-sha256"] == old_digest

    payload = initial.json()
    payload["generated_at"] = "2026-07-29 12:00"
    new_blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    dataset_replacement = datasets_dir / ".media-prices.json.next"
    dataset_replacement.write_bytes(new_blob)
    dataset_replacement.replace(datasets_dir / "media-prices.json")

    between_replacements = client.get("/api/v2/datasets/media-prices", headers=admin_headers)
    assert between_replacements.status_code == 200
    assert between_replacements.content == new_blob
    assert between_replacements.headers["x-dataset-sha256"] == old_digest

    new_digest = hashlib.sha256(new_blob).hexdigest()
    sidecar_replacement = datasets_dir / ".media-prices.sha256.next"
    sidecar_replacement.write_text(
        f"{new_digest}  media-prices.json\n",
        encoding="utf-8",
    )
    sidecar_replacement.replace(datasets_dir / "media-prices.sha256")

    after_sidecar = client.get("/api/v2/datasets/media-prices", headers=admin_headers)
    assert after_sidecar.status_code == 200
    assert after_sidecar.content == new_blob
    assert after_sidecar.headers["x-dataset-sha256"] == new_digest
    assert hashlib.sha256(after_sidecar.content).hexdigest() == new_digest


def test_media_prices_dataset_is_publicly_readable(datasets_dir: Path) -> None:
    digest = _write_dataset(datasets_dir)
    client = TestClient(app)
    response = client.get("/api/v2/datasets/media-prices")
    assert response.status_code == 200
    assert response.headers["x-dataset-sha256"] == digest
    assert response.json()["rows"][0]["name"] == "示例媒体"


def test_media_prices_dataset_does_not_reject_customer_session(datasets_dir: Path) -> None:
    _write_dataset(datasets_dir)
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    customer_headers = _member_headers(
        client, admin_headers, f"datasets-customer-{suffix}", "customer"
    )
    response = client.get("/api/v2/datasets/media-prices", headers=customer_headers)
    assert response.status_code == 200


def test_media_prices_dataset_missing_file_returns_404(datasets_dir: Path) -> None:
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    response = client.get("/api/v2/datasets/media-prices", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_media_wemedia_dataset_is_separate_and_integrity_checked(datasets_dir: Path) -> None:
    digest = _write_wemedia_dataset(datasets_dir)
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"wemedia-admin-{suffix}")
    operator_headers = _member_headers(
        client, admin_headers, f"wemedia-operator-{suffix}", "operator"
    )
    response = client.get("/api/v2/datasets/media-wemedia", headers=operator_headers)
    assert response.status_code == 200, response.text
    assert response.headers["x-dataset-sha256"] == digest
    assert response.json()["rows"][0]["platform"] == "百家号"
    assert hashlib.sha256(response.content).hexdigest() == digest


def test_media_wemedia_dataset_is_publicly_readable(datasets_dir: Path) -> None:
    digest = _write_wemedia_dataset(datasets_dir)
    client = TestClient(app)
    response = client.get("/api/v2/datasets/media-wemedia")
    assert response.status_code == 200
    assert response.headers["x-dataset-sha256"] == digest
    assert response.json()["rows"][0]["name"] == "示例账号"


def test_media_wemedia_dataset_does_not_reject_customer_session(datasets_dir: Path) -> None:
    _write_wemedia_dataset(datasets_dir)
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant_pub_id, admin_headers = _bootstrap(client, f"wemedia-admin-{suffix}")
    customer_headers = _member_headers(
        client, admin_headers, f"wemedia-customer-{suffix}", "customer"
    )
    response = client.get("/api/v2/datasets/media-wemedia", headers=customer_headers)
    assert response.status_code == 200


_STUB_REFRESH_SCRIPT = """\
import json, os, pathlib
base = pathlib.Path(os.environ["MEDIA_PRICES_DATASETS_DIR"])
payload = {
    "state": "done",
    "started_at": "2026-07-27 16:00:00",
    "updated_at": "2026-07-27 16:00:01",
    "message": "stub 1",
    "sources": {"prfabu": {"status": "ok", "rows": 1, "note": ""}},
}
(base / "media-prices.refresh.json").write_text(json.dumps(payload), encoding="utf-8")
"""


def _write_refresh_status(directory: Path, state: str = "done") -> None:
    payload = {
        "state": state,
        "started_at": "2026-07-27 16:00:00",
        "updated_at": "2026-07-27 16:01:00",
        "message": "prfabu 19087 · 投媒网 10004(限流)",
        "sources": {
            "prfabu": {"status": "ok", "rows": 19087, "note": ""},
            "toumeiw": {"status": "partial", "rows": 10004, "note": "rate_limited"},
        },
    }
    (directory / "media-prices.refresh.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_media_prices_refresh_requires_operate_permission(datasets_dir: Path) -> None:
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    reviewer = _member_headers(client, admin_headers, f"datasets-reviewer-{suffix}", "reviewer")
    customer = _member_headers(client, admin_headers, f"datasets-customer-{suffix}", "customer")
    for headers in (reviewer, customer):
        response = client.post("/api/v2/datasets/media-prices/refresh", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"


def test_media_prices_refresh_conflict_when_lock_present(datasets_dir: Path) -> None:
    (datasets_dir / "media-prices.refresh.lock").write_text("pid=1\n", encoding="utf-8")
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    operator = _member_headers(client, admin_headers, f"datasets-operator-{suffix}", "operator")
    response = client.post("/api/v2/datasets/media-prices/refresh", headers=operator)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "refresh_already_running"


def test_media_prices_refresh_spawns_pipeline_and_reports_done(
    datasets_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = datasets_dir / "stub_refresh.py"
    stub.write_text(_STUB_REFRESH_SCRIPT, encoding="utf-8")
    monkeypatch.setattr(datasets_router, "_REFRESH_SCRIPT", stub)
    monkeypatch.setenv("MEDIA_PRICES_DATASETS_DIR", str(datasets_dir))
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    operator = _member_headers(client, admin_headers, f"datasets-operator-{suffix}", "operator")

    response = client.post("/api/v2/datasets/media-prices/refresh", headers=operator)
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "running"

    status_file = datasets_dir / "media-prices.refresh.json"
    for _ in range(100):
        if status_file.exists():
            break
        time.sleep(0.1)
    assert status_file.exists(), "stub refresh pipeline did not write refresh.json"

    status = client.get("/api/v2/datasets/media-prices/refresh-status", headers=operator)
    assert status.status_code == 200
    assert status.headers["cache-control"] == "private, no-store"
    payload = status.json()
    assert payload["state"] == "done"
    assert payload["sources"]["prfabu"] == {"status": "ok", "rows": 1, "note": ""}


def test_media_prices_refresh_status_never_and_done(datasets_dir: Path) -> None:
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, admin_headers = _bootstrap(client, f"datasets-admin-{suffix}")
    operator = _member_headers(client, admin_headers, f"datasets-operator-{suffix}", "operator")

    never = client.get("/api/v2/datasets/media-prices/refresh-status", headers=operator)
    assert never.status_code == 200
    assert never.json()["state"] == "never"

    _write_refresh_status(datasets_dir)
    done = client.get("/api/v2/datasets/media-prices/refresh-status", headers=operator)
    assert done.status_code == 200
    payload = done.json()
    assert payload["state"] == "done"
    assert payload["message"] == "prfabu 19087 · 投媒网 10004(限流)"
    assert payload["sources"]["toumeiw"]["status"] == "partial"
    assert payload["sources"]["toumeiw"]["rows"] == 10004
