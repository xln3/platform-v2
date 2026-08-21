from datetime import UTC, datetime, timedelta

import pytest

from tools import topup_sbaq_g0134_20260816 as planner
from tools.topup_sbaq_g0134_20260816 import (
    GROUPS,
    LEGS,
    TARGET,
    _api_groups,
    coverage_modes,
    deficit_groups,
    evaluate_launch_health,
    requested_mode,
)

_NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)


def _counts(value: int) -> dict[str, int]:
    return {query: value for _, _, queries in GROUPS for query in queries}


def test_formal_scope_has_all_34_groups_and_six_legs() -> None:
    assert [number for number, _, _ in GROUPS] == list(range(1, 35))
    assert all(len(queries) == 4 for _, _, queries in GROUPS)
    assert set(LEGS) == {
        "doubao-bj",
        "doubao-sh",
        "deepseek-bj",
        "deepseek-sh",
        "yiyan-bj",
        "yiyan-sh",
    }


def test_doubao_topups_use_quick_mode_but_count_prior_expert_answers() -> None:
    assert requested_mode("doubao-bj") == "normal"
    assert requested_mode("doubao-sh") == "normal"
    assert coverage_modes("doubao-bj") == ("deep_think", "normal")
    assert coverage_modes("doubao-sh") == ("deep_think", "normal")

    for leg in ("deepseek-bj", "deepseek-sh", "yiyan-bj", "yiyan-sh"):
        assert requested_mode(leg) == "deep_think"
        assert coverage_modes(leg) == ("deep_think",)


def test_deficit_planner_caps_batch_and_preserves_original_group_number() -> None:
    counts = _counts(TARGET)
    first_g04_query = GROUPS[3][2][0]
    second_g04_query = GROUPS[3][2][1]
    counts[first_g04_query] = 0
    counts[second_g04_query] = 1

    groups = deficit_groups(counts, max_queries=2)

    assert groups == [
        {
            "name": GROUPS[3][1],
            "group_number": 4,
            "items": [
                {"text": first_g04_query, "priority": 1},
                {"text": second_g04_query, "priority": 2},
            ],
        }
    ]
    assert _api_groups(groups) == [{"name": GROUPS[3][1], "items": groups[0]["items"]}]


def test_deficit_planner_refuses_to_invent_work_when_complete() -> None:
    assert deficit_groups(_counts(TARGET), max_queries=4) == []


def _health_rows() -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    region: dict[str, object] = {
        "state": "ok",
        "last_probe_at": _NOW - timedelta(minutes=5),
        "last_probe_ok": True,
    }
    browser: dict[str, object] = {
        "instance_key": "doubao_bj",
        "platform": "doubao",
        "region_gb": "110000",
        "activity": "idle",
        "error_streak": 0,
        "breaker_until": None,
        "muted_until": None,
    }
    accounts: list[dict[str, object]] = [
        {
            "phone_state": "active",
            "runtime_state": "idle",
            "current_run_pub_id": None,
            "browser_instance_key": "doubao_bj",
            "quota_day": None,
            "quota_week": None,
            "quota_year": None,
            "used_today": 0,
            "used_week": 0,
            "used_year": 0,
            "quota_reset_at": None,
            "quota_probe_json": None,
        }
    ]
    return region, browser, accounts


def _evaluate(
    region: dict[str, object] | None,
    browser: dict[str, object] | None,
    accounts: list[dict[str, object]],
) -> dict[str, object]:
    return evaluate_launch_health(
        region=region,
        browser=browser,
        accounts=accounts,
        expected_browser_key="doubao_bj",
        platform="doubao",
        region_gb="110000",
        mode="normal",
        now=_NOW,
    )


def test_launch_health_accepts_only_recent_confirmed_and_governed_leg() -> None:
    region, browser, accounts = _health_rows()
    assert _evaluate(region, browser, accounts) == {
        "ok": True,
        "reason": None,
        "reasons": [],
        "warnings": [],
        "governance": "managed",
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda region, browser: region.update(last_probe_ok=False), "region_latest_probe_not_ok"),
        (
            lambda region, browser: region.update(last_probe_at=_NOW - timedelta(minutes=26)),
            "region_probe_stale",
        ),
        (lambda region, browser: browser.update(activity="captcha"), "browser_activity_captcha"),
        (
            lambda region, browser: browser.update(error_streak=41, breaker_until=_NOW),
            "browser_failure_unrecovered",
        ),
    ],
)
def test_launch_health_blocks_transient_or_unrecovered_roots(mutation: object, reason: str) -> None:
    region, browser, accounts = _health_rows()
    mutation(region, browser)  # type: ignore[operator]
    health = _evaluate(region, browser, accounts)
    assert health["ok"] is False
    assert reason in health["reasons"]


def test_launch_health_blocks_unregistered_doubao_leg() -> None:
    region, browser, _ = _health_rows()
    health = _evaluate(region, browser, [])
    assert health["ok"] is False
    assert health["reason"] == "account_unregistered"
    assert health["governance"] == "required_unmanaged"
    assert health["warnings"] == []


def test_launch_health_allows_unmanaged_legacy_platform_with_warning() -> None:
    region, browser, _ = _health_rows()
    browser.update(instance_key="yiyan_bj", platform="yiyan")

    health = evaluate_launch_health(
        region=region,
        browser=browser,
        accounts=[],
        expected_browser_key="yiyan_bj",
        platform="yiyan",
        region_gb="110000",
        mode="deep_think",
        now=_NOW,
    )

    assert health["ok"] is True
    assert health["reason"] is None
    assert health["governance"] == "legacy_unmanaged"
    assert health["warnings"] == ["legacy_unmanaged"]


def test_launch_health_honors_only_requested_mode_quota_block() -> None:
    region, browser, accounts = _health_rows()
    accounts[0]["quota_probe_json"] = {
        "mode_quota_blocks": {"deep_think": {"resume_at": (_NOW + timedelta(hours=1)).isoformat()}}
    }
    assert _evaluate(region, browser, accounts)["ok"] is True
    accounts[0]["quota_probe_json"] = {
        "mode_quota_blocks": {"normal": {"resume_at": (_NOW + timedelta(hours=1)).isoformat()}}
    }
    health = _evaluate(region, browser, accounts)
    assert health["ok"] is False
    assert "no_collectable_account" in health["reasons"]


def test_launch_refuses_before_opening_api_client_when_health_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        planner,
        "require_launch_health",
        lambda leg, phase: (_ for _ in ()).throw(planner.LaunchHealthError(phase)),
    )
    monkeypatch.setattr(
        planner, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not open client"))
    )
    with pytest.raises(planner.LaunchHealthError, match="pre_freeze"):
        planner.launch("doubao-bj", "pass", [{"name": "g", "items": []}])


def test_launch_rechecks_health_after_freeze_and_never_creates_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases: list[str] = []

    def gate(leg: str, *, phase: str) -> dict[str, object]:
        phases.append(phase)
        if phase == "pre_run":
            raise planner.LaunchHealthError(phase)
        return {"ok": True}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"pub_id": "cfv_frozen"}

    class Client:
        def __init__(self) -> None:
            self.posts: list[str] = []

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, path: str) -> Response:
            return Response()

        def post(self, path: str, **kwargs: object) -> Response:
            self.posts.append(path)
            return Response()

    client = Client()
    monkeypatch.setattr(planner, "require_launch_health", gate)
    monkeypatch.setattr(planner, "_client", lambda: client)
    with pytest.raises(planner.LaunchHealthError, match="pre_run"):
        planner.launch("doubao-bj", "pass", [{"name": "g", "items": []}])
    assert phases == ["pre_freeze", "pre_run"]
    assert client.posts == [f"/api/v2/projects/{planner.PROJECT}/config/freeze"]
