from __future__ import annotations

from types import SimpleNamespace

from _pytest.config import ExitCode
from conftest import pytest_sessionfinish


class _Reporter:
    def __init__(self, skipped: int) -> None:
        self.stats = {"skipped": [object()] * skipped}
        self.messages: list[str] = []

    def write_sep(self, _separator: str, message: str) -> None:
        self.messages.append(message)


def _session(
    *, fail_on_skip: bool, skipped: int, exitstatus: ExitCode
) -> tuple[SimpleNamespace, _Reporter]:
    reporter = _Reporter(skipped)
    config = SimpleNamespace(
        getoption=lambda option: fail_on_skip if option == "--fail-on-skip" else False,
        pluginmanager=SimpleNamespace(get_plugin=lambda _name: reporter),
    )
    return SimpleNamespace(config=config, exitstatus=exitstatus), reporter


def test_fail_on_skip_turns_an_otherwise_passing_lane_red() -> None:
    session, reporter = _session(
        fail_on_skip=True,
        skipped=2,
        exitstatus=ExitCode.OK,
    )

    pytest_sessionfinish(session, ExitCode.OK)

    assert session.exitstatus == ExitCode.TESTS_FAILED
    assert reporter.messages == ["selected test lane skipped 2 item(s)"]


def test_fail_on_skip_does_not_mask_an_existing_failure() -> None:
    session, _reporter = _session(
        fail_on_skip=True,
        skipped=1,
        exitstatus=ExitCode.INTERRUPTED,
    )

    pytest_sessionfinish(session, ExitCode.INTERRUPTED)

    assert session.exitstatus == ExitCode.INTERRUPTED


def test_skip_policy_is_inert_without_the_option_or_skips() -> None:
    for fail_on_skip, skipped in ((False, 1), (True, 0)):
        session, reporter = _session(
            fail_on_skip=fail_on_skip,
            skipped=skipped,
            exitstatus=ExitCode.OK,
        )

        pytest_sessionfinish(session, ExitCode.OK)

        assert session.exitstatus == ExitCode.OK
        assert reporter.messages == []
