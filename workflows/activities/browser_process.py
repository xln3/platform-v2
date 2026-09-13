"""Killable browser execution, isolated from the long-lived Temporal worker.

Only children created by this execution join its process group. Resident browsers
are external CDP targets and are never members of that group.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import queue
import signal
import uuid
from collections.abc import Awaitable, Callable
from typing import Any


def _entry(
    worker: Callable[..., Any],
    args: tuple[Any, ...],
    events: Any,
    stop: Any,
    holder: str,
) -> None:
    os.setsid()
    os.environ["GEO_BROWSER_FENCE_HOLDER"] = holder
    events.put(("ready", os.getpid()))
    try:
        result = worker(*args, events=events, stop=stop)
    except BaseException as exc:
        events.put(
            (
                "error",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "wall_type": getattr(exc, "wall_type", None),
                    "evidence_path": getattr(exc, "evidence_path", None),
                    "evidence_refs": getattr(exc, "evidence_refs", []),
                },
            )
        )
    else:
        events.put(("result", result))


class BrowserProcessError(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail["message"])
        self.detail = detail


async def run_isolated_browser(
    worker: Callable[..., Any],
    args: tuple[Any, ...],
    *,
    on_event: Callable[[str, Any], None],
    shutdown_grace_s: float = 3.0,
    on_stopped: Callable[[str], Awaitable[None]] | None = None,
) -> Any:
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    stop = context.Event()
    holder = f"browser-process:{uuid.uuid4().hex}"
    process = context.Process(target=_entry, args=(worker, args, events, stop, holder), daemon=True)
    process.start()
    process_id = process.pid
    assert process_id is not None
    group_ready = False
    try:
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                if not process.is_alive():
                    # Recheck after observing exit: the feeder may have flushed
                    # between our first empty read and is_alive().
                    try:
                        kind, value = events.get_nowait()
                    except queue.Empty:
                        raise BrowserProcessError(
                            {
                                "type": "BrowserProcessExited",
                                "message": (
                                    f"browser process exited without result: {process.exitcode}"
                                ),
                            }
                        ) from None
                else:
                    await asyncio.sleep(0.02)
                    continue
            if kind == "ready":
                group_ready = value == process.pid
            elif kind == "result":
                return value
            elif kind == "error":
                raise BrowserProcessError(value)
            else:
                on_event(kind, value)
    finally:
        stop.set()
        deadline = asyncio.get_running_loop().time() + shutdown_grace_s
        while process.is_alive() and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
        if process.is_alive():
            # Verify ownership even when cancellation arrived before the ready event.
            try:
                group_ready = group_ready or os.getpgid(process_id) == process_id
                if group_ready:
                    os.killpg(process_id, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        process.join(timeout=0.5)
        events.close()
        events.cancel_join_thread()
        if not process.is_alive() and on_stopped is not None:
            await on_stopped(holder)
