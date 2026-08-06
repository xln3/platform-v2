"""任务间拟人节奏（inter_task_delay_seconds）口径测试。"""

from workflows.definitions.collection import inter_task_delay_seconds


def test_first_task_has_no_delay() -> None:
    assert inter_task_delay_seconds(0.5, 0, 45.0, 150.0) == 0.0


def test_delay_within_bounds() -> None:
    for rand in (0.0, 0.25, 0.5, 0.75, 0.999999):
        value = inter_task_delay_seconds(rand, 1, 45.0, 150.0)
        assert 45.0 <= value < 150.0


def test_delay_endpoints() -> None:
    assert inter_task_delay_seconds(0.0, 2, 45.0, 150.0) == 45.0
    assert inter_task_delay_seconds(0.999999, 2, 45.0, 150.0) > 100.0


def test_max_below_min_is_clamped_to_min() -> None:
    assert inter_task_delay_seconds(0.7, 1, 60.0, 30.0) == 60.0


def test_disabled_when_max_not_positive() -> None:
    assert inter_task_delay_seconds(0.7, 1, 45.0, 0.0) == 0.0
    assert inter_task_delay_seconds(0.7, 1, 45.0, -5.0) == 0.0
