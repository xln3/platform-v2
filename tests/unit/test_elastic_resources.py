from datetime import UTC, datetime, timedelta

from geo_platform.collection.elastic_resources import BrowserCapacity, plan_browser_capacity

NOW = datetime(2026, 9, 12, tzinfo=UTC)
OLD = NOW - timedelta(hours=1)


def test_idle_reclamation_makes_room_for_reserved_lane() -> None:
    actions = plan_browser_capacity(
        [
            BrowserCapacity("doubao_bj", True, False, False, OLD),
            BrowserCapacity("deepseek_sh", False, True, True, NOW),
        ],
        now=NOW,
        max_active=1,
    )
    assert [(a.key, a.action) for a in actions] == [
        ("doubao_bj", "stop"),
        ("deepseek_sh", "start"),
    ]


def test_captcha_and_inflight_capacity_are_never_reclaimed() -> None:
    actions = plan_browser_capacity(
        [
            BrowserCapacity("doubao_bj", True, False, True, OLD),
            BrowserCapacity("deepseek_sh", False, True, True, NOW),
        ],
        now=NOW,
        max_active=1,
    )
    assert [(a.key, a.action) for a in actions] == [("deepseek_sh", "wait")]


def test_cooldown_avoids_start_stop_flapping() -> None:
    assert (
        plan_browser_capacity(
            [
                BrowserCapacity("doubao_bj", True, False, False, NOW),
            ],
            now=NOW,
        )
        == []
    )
