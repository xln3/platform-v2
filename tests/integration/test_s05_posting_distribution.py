from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from geo_platform.config import get_settings
from geo_platform.main import app
from geo_platform.posting.providers import ProviderResult, ProviderSubmission

from tests.unit.test_posting_docx import build_docx


def _bootstrap(client: TestClient, subject: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v2/identity/bootstrap",
        headers={"X-Bootstrap-Secret": "development-bootstrap"},
        json={"tenant_name": subject, "subject": subject, "display_name": "Admin"},
    )
    assert response.status_code == 201
    return response.json()["tenant_pub_id"], {
        "X-Tenant-Id": response.json()["tenant_pub_id"],
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
def posting_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("GEO_DATASETS_DIR", str(tmp_path))
    get_settings.cache_clear()
    payload = {
        "generated_at": "2026-07-29 10:00",
        "rows": [
            {
                "name": "GEO测试媒体",
                "prices": {"prfabu": 88},
                "ids": {"prfabu": 12345},
            }
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(blob).hexdigest()
    (tmp_path / "media-prices.json").write_bytes(blob)
    (tmp_path / "media-prices.sha256").write_text(
        f"{digest}  media-prices.json\n",
        encoding="utf-8",
    )
    yield tmp_path
    get_settings.cache_clear()


class FakeProvider:
    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        assert submission.provider == "prfabu"
        assert submission.provider_media_id == "12345"
        assert submission.title == "自动发帖测试标题"
        assert "图文正文" in submission.content_html
        return ProviderResult(
            status="submitted",
            message="供应商已收稿",
            external_order_id="order-001",
        )

    def refresh(
        self,
        *,
        catalog_type: str,
        external_order_id: str,
        media_name: str,
        title: str,
    ) -> ProviderResult:
        assert catalog_type == "news"
        assert external_order_id == "order-001"
        assert media_name == "GEO测试媒体"
        assert title == "自动发帖测试标题"
        return ProviderResult(
            status="published",
            message="已出稿",
            external_order_id=external_order_id,
            public_url="https://media.example.com/article/1",
        )


class BalanceInsufficientProvider(FakeProvider):
    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        assert submission.provider_media_id == "12345"
        return ProviderResult(
            status="balance_insufficient",
            message="余额不足，请充值",
        )


def _create_batch(
    client: TestClient,
    headers: dict[str, str],
    *,
    key: str,
    maximum: str = "88.00",
    auto_submit: bool = True,
) -> object:
    return client.post(
        "/api/v2/posting/batches",
        headers={**headers, "Idempotency-Key": key},
        files={
            "document": (
                "article.docx",
                build_docx(title="自动发帖测试标题", body="图文正文"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={
            "targets_json": json.dumps(
                [
                    {
                        "catalog_type": "news",
                        "provider": "prfabu",
                        "media_name": "GEO测试媒体",
                        "media_platform": "",
                    }
                ],
                ensure_ascii=False,
            ),
            "title": "",
            "customer_name": "测试品牌",
            "auto_submit": str(auto_submit).lower(),
            "confirm_spend": str(auto_submit).lower(),
            "max_total_amount": maximum,
            "note": "测试发稿",
        },
    )


def test_docx_batch_auto_submits_and_refreshes_per_media_status(
    posting_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del posting_dataset
    monkeypatch.setattr("geo_platform.posting.service.provider_for", lambda _name: FakeProvider())
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, headers = _bootstrap(client, f"posting-admin-{suffix}")
    key = f"posting-test-{suffix}-0001"

    created = _create_batch(client, headers, key=key)
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["title"] == "自动发帖测试标题"
    assert batch["content_text"].startswith("自动发帖测试标题\n\n图文正文")
    assert batch["image_count"] == 1
    assert batch["quoted_total_amount"] == "88.00"
    assert batch["status"] == "queued"
    assert batch["approval_state"] == "approved"
    assert batch["approval_requested_by_pub_id"] == batch["approved_by_pub_id"]
    completed = client.get(
        f"/api/v2/posting/batches/{batch['pub_id']}",
        headers=headers,
    )
    assert completed.status_code == 200
    batch = completed.json()
    assert batch["status"] == "submitted"
    assert (
        batch["targets"][0]
        | {
            "provider": "prfabu",
            "media_name": "GEO测试媒体",
            "status": "submitted",
            "external_order_id": "order-001",
        }
        == batch["targets"][0]
    )

    replay = _create_batch(client, headers, key=key)
    assert replay.status_code == 201
    assert replay.json()["pub_id"] == batch["pub_id"]
    listed = client.get("/api/v2/posting/batches", headers=headers)
    assert listed.status_code == 200
    assert [item["pub_id"] for item in listed.json()].count(batch["pub_id"]) == 1

    refreshed = client.post(
        f"/api/v2/posting/batches/{batch['pub_id']}/refresh",
        headers=headers,
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "published"
    target = refreshed.json()["targets"][0]
    assert target["status"] == "published"
    assert target["public_url"] == "https://media.example.com/article/1"
    assert any(event["to_status"] == "published" for event in refreshed.json()["events"])


def test_posting_enforces_budget_permission_and_tenant_isolation(
    posting_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del posting_dataset
    monkeypatch.setattr("geo_platform.posting.service.provider_for", lambda _name: FakeProvider())
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, headers = _bootstrap(client, f"posting-budget-admin-{suffix}")
    too_low = _create_batch(
        client,
        headers,
        key=f"posting-test-{suffix}-budget",
        maximum="87.99",
    )
    assert too_low.status_code == 409
    assert too_low.json()["error"]["code"] == "posting_invalid_state"

    customer_headers = _member_headers(
        client,
        headers,
        f"posting-customer-{suffix}",
        "customer",
    )
    forbidden = _create_batch(
        client,
        customer_headers,
        key=f"posting-test-{suffix}-customer",
    )
    assert forbidden.status_code == 403

    created = _create_batch(client, headers, key=f"posting-test-{suffix}-valid")
    assert created.status_code == 201
    _other_tenant, other_headers = _bootstrap(client, f"posting-other-{suffix}")
    hidden = client.get(
        f"/api/v2/posting/batches/{created.json()['pub_id']}",
        headers=other_headers,
    )
    assert hidden.status_code == 404


def test_draft_starts_after_the_same_operator_confirms_spend(
    posting_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del posting_dataset
    monkeypatch.setattr("geo_platform.posting.service.provider_for", lambda _name: FakeProvider())
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, headers = _bootstrap(client, f"posting-draft-admin-{suffix}")
    created = _create_batch(
        client,
        headers,
        key=f"posting-test-{suffix}-draft",
        auto_submit=False,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"
    assert created.json()["approval_state"] == "draft"

    started = client.post(
        f"/api/v2/posting/batches/{created.json()['pub_id']}/submit",
        headers=headers,
        json={"confirm_spend": True, "max_total_amount": "88.00"},
    )
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "queued"
    assert started.json()["approval_state"] == "approved"
    assert started.json()["approved_by_pub_id"] == started.json()["created_by_pub_id"]

    completed = client.get(
        f"/api/v2/posting/batches/{created.json()['pub_id']}",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "submitted"
    assert completed.json()["targets"][0]["external_order_id"] == "order-001"


def test_posting_exposes_balance_insufficient_as_target_and_batch_status(
    posting_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del posting_dataset
    monkeypatch.setattr(
        "geo_platform.posting.service.provider_for",
        lambda _name: BalanceInsufficientProvider(),
    )
    client = TestClient(app)
    suffix = secrets.token_hex(6)
    _tenant, headers = _bootstrap(client, f"posting-balance-admin-{suffix}")
    created = _create_batch(client, headers, key=f"posting-test-{suffix}-balance")
    assert created.status_code == 201
    detail = client.get(
        f"/api/v2/posting/batches/{created.json()['pub_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "blocked"
    assert detail.json()["targets"][0]["status"] == "balance_insufficient"
    assert detail.json()["targets"][0]["provider_message"] == "余额不足，请充值"

    monkeypatch.setattr("geo_platform.posting.service.provider_for", lambda _name: FakeProvider())
    retried = client.post(
        f"/api/v2/posting/batches/{created.json()['pub_id']}/submit",
        headers=headers,
        json={"confirm_spend": True, "max_total_amount": "88.00"},
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "queued"
    recovered = client.get(
        f"/api/v2/posting/batches/{created.json()['pub_id']}",
        headers=headers,
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "submitted"
    assert recovered.json()["targets"][0]["status"] == "submitted"
