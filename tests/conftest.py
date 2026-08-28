"""Repository-wide test execution policy.

Optional test lanes are selected explicitly with pytest markers.  Once a lane is
selected, a skip is a broken precondition rather than a successful test result;
CI enables ``--fail-on-skip`` to enforce that contract.
"""

from __future__ import annotations

from _pytest.config import ExitCode, Parser
from _pytest.main import Session


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("geo-test-policy")
    group.addoption(
        "--fail-on-skip",
        action="store_true",
        default=False,
        help="return a failing exit code when any selected test is skipped",
    )


def pytest_sessionfinish(session: Session, exitstatus: int | ExitCode) -> None:
    if not session.config.getoption("--fail-on-skip"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = () if reporter is None else reporter.stats.get("skipped", ())
    if not skipped:
        return
    if reporter is not None:
        reporter.write_sep("=", f"selected test lane skipped {len(skipped)} item(s)")
    if session.exitstatus == ExitCode.OK:
        session.exitstatus = ExitCode.TESTS_FAILED
