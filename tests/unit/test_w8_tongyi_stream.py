"""wait_finish 多阶段流口径（2026-08-07 live 校准：深搜检索流先完、生成流后完）。"""

from __future__ import annotations

from workflows.activities.tongyi_adapter import _EventStreamCapture


class _FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


def _capture() -> _EventStreamCapture:
    capture = object.__new__(_EventStreamCapture)  # 绕过 CDP attach，直接置内部态
    capture._stream_request_ids = []
    capture._loading_finished = set()
    capture._loading_failed = set()
    capture._bytes = {}
    return capture


def test_wait_finish_follows_latest_stream() -> None:
    capture = _capture()
    capture._stream_request_ids.extend(["s1", "s2"])
    capture._loading_finished.add("s1")  # 检索流先完
    capture._bytes["s1"] = 800
    capture._bytes["s2"] = 12000
    page = _FakePage()
    meta = capture.wait_finish(
        page, appearance_timeout_s=1.0, timeout_s=1.0, dom_settle_s=0.01, stream_settle_s=0.01
    )
    # s1 已完但 s2 未完：直到 s2 完成才算 finished（settle 窗内无新流）
    assert meta["finished"] is False  # s2 未完结 → 不判完成
    capture._loading_finished.add("s2")
    meta = capture.wait_finish(
        page, appearance_timeout_s=1.0, timeout_s=30.0, dom_settle_s=0.01, stream_settle_s=0.01
    )
    assert meta["found"] is True
    assert meta["finished"] is True
    assert meta["bytes_received"] == 12000
    assert meta["streams_seen"] == 2


def test_wait_finish_returns_elapsed_when_no_stream() -> None:
    capture = _capture()
    page = _FakePage()
    meta = capture.wait_finish(
        page, appearance_timeout_s=0.5, timeout_s=5.0, dom_settle_s=0.01, stream_settle_s=0.01
    )
    assert meta["found"] is False
    assert meta["finished"] is False
