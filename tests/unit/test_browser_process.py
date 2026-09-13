import asyncio
import os
import time
from typing import Any

import pytest

from workflows.activities.browser_process import BrowserProcessError, run_isolated_browser


def _hang(*, events: Any, stop: Any) -> None:
    events.put(("pid", os.getpid()))
    while True:
        time.sleep(1)  # Deliberately ignores cooperative cancellation.


def _finish(*, events: Any, stop: Any) -> dict[str, str]:
    events.put(("stage", "done"))
    return {"answer": "real result"}


def _fail(*, events: Any, stop: Any) -> None:
    raise ValueError("observed failure")


async def test_hung_process_is_killed_before_cancellation_returns() -> None:
    seen: list[int] = []
    ready = asyncio.Event()

    def event(kind: str, value: Any) -> None:
        seen.append(value)
        ready.set()

    released = []

    async def stopped(holder: str) -> None:
        with pytest.raises(ProcessLookupError):
            os.kill(seen[0], 0)
        released.append(holder)

    task = asyncio.create_task(
        run_isolated_browser(_hang, (), on_event=event, shutdown_grace_s=0.05, on_stopped=stopped)
    )
    await asyncio.wait_for(ready.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    with pytest.raises(ProcessLookupError):
        os.kill(seen[0], 0)
    assert len(released) == 1 and released[0].startswith("browser-process:")


async def test_process_returns_answer_and_progress() -> None:
    seen = []
    result = await run_isolated_browser(_finish, (), on_event=lambda *args: seen.append(args))
    assert result == {"answer": "real result"}
    assert seen == [("stage", "done")]


async def test_process_failure_retains_observed_error() -> None:
    with pytest.raises(BrowserProcessError) as exc:
        await run_isolated_browser(_fail, (), on_event=lambda *_: None)
    assert exc.value.detail["type"] == "ValueError"
