from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workflows.activities.elastic_proxy import ProxyPurchaseBudget, ensure_region_capacity
from workflows.activities.region_proxy_router import RegionProxyError

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def test_spend_waits_for_remote_notification_receipt(tmp_path: Path) -> None:
    class Router:
        def resolve(self, *_args):
            raise RegionProxyError("proxy_lease_unavailable", "none", non_retryable=True)

        def acquire_paid(self, *_args, **_kwargs):
            raise AssertionError("must not spend without remote receipt")

    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        result = ensure_region_capacity(
            "110000",
            router=Router(),  # type: ignore[arg-type]
            budget=budget,
            daily_limit=3,
            apply=True,
            now=NOW,
            notify_needed=lambda *_: False,
        )
        assert result["reason"] == "notification_pending"
        assert budget.connection.execute("select count(*) from purchase_attempt").fetchone()[0] == 0
    finally:
        budget.close()


def test_hard_daily_ceiling_cannot_be_overridden(tmp_path: Path) -> None:
    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        with pytest.raises(ValueError):
            budget.reserve("110000", daily_limit=4, now=NOW)
        for region in ["110000", "120000", "310000"]:
            assert budget.reserve(region, daily_limit=3, now=NOW)
        assert not budget.reserve("440000", daily_limit=3, now=NOW)
    finally:
        budget.close()


def test_day_rolls_over_at_tokyo_midnight(tmp_path: Path) -> None:
    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        before = datetime(2026, 9, 12, 14, 59, tzinfo=UTC)
        after = before + timedelta(minutes=2)
        assert budget.reserve("110000", daily_limit=1, now=before)
        budget.complete("110000", now=before, state="fulfilled")
        assert budget.reserve("110000", daily_limit=1, now=after)
    finally:
        budget.close()


def test_purchase_budget_survives_restart_and_blocks_uncertain_repeat(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    first = ProxyPurchaseBudget(path)
    assert first.reserve("110000", daily_limit=1, now=NOW)
    first.close()
    second = ProxyPurchaseBudget(path)
    try:
        assert not second.reserve("310000", daily_limit=1, now=NOW)
        assert not second.reserve("110000", daily_limit=1, now=NOW + timedelta(days=1))
        assert second.reserve("310000", daily_limit=1, now=NOW + timedelta(days=1))
    finally:
        second.close()


def test_completed_purchase_keeps_daily_limit(tmp_path: Path) -> None:
    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        assert budget.reserve("110000", daily_limit=1, now=NOW)
        budget.complete("110000", now=NOW, state="fulfilled")
        assert not budget.reserve("310000", daily_limit=1, now=NOW)
    finally:
        budget.close()


def test_rejected_purchase_retries_after_cooldown(tmp_path: Path) -> None:
    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        assert budget.reserve("110000", daily_limit=1, now=NOW)
        budget.complete("110000", now=NOW, state="rejected")
        assert not budget.reserve("110000", daily_limit=1, now=NOW + timedelta(minutes=5))
        assert budget.reserve("110000", daily_limit=1, now=NOW + timedelta(minutes=16))
    finally:
        budget.close()


def test_provider_reconciliation_closes_uncertain_intent(tmp_path: Path) -> None:
    budget = ProxyPurchaseBudget(tmp_path / "journal.sqlite3")
    try:
        assert budget.reserve("110000", daily_limit=1, now=NOW)
        budget.reconcile_available("110000")
        assert budget.reserve("110000", daily_limit=1, now=NOW + timedelta(days=31))
    finally:
        budget.close()
