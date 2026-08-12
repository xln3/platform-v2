from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from workflows.workers import main as worker_main


@pytest.mark.asyncio
async def test_main_worker_preflights_ocr_before_temporal_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    registered: dict[str, Any] = {}

    def preflight() -> str:
        events.append("ocr_preflight")
        return "rapidocr-test+onnxruntime-test"

    class FakeClient:
        @staticmethod
        async def connect(*_args: Any, **_kwargs: Any) -> object:
            events.append("temporal_connect")
            return object()

    class FakeWorker:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            events.append("worker_register")
            registered.update(kwargs)

        async def run(self) -> None:
            events.append("worker_run")

    monkeypatch.setattr(worker_main, "preflight_answer_evidence_ocr", preflight)
    monkeypatch.setattr(worker_main, "Client", FakeClient)
    monkeypatch.setattr(worker_main, "Worker", FakeWorker)
    monkeypatch.setattr(worker_main, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(worker_main, "configure_tracing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            temporal_address="temporal:7233",
            temporal_namespace="default",
            temporal_task_queue="collection-test",
        ),
    )

    await worker_main.run_worker()

    assert events == ["ocr_preflight", "temporal_connect", "worker_register", "worker_run"]
    assert worker_main.persist_collection_result in registered["activities"]
    assert worker_main._collect_with_adapter_impl in registered["activities"]


@pytest.mark.asyncio
async def test_main_worker_does_not_connect_when_ocr_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = False

    def failed_preflight() -> str:
        raise RuntimeError("broken OCR runtime")

    class FakeClient:
        @staticmethod
        async def connect(*_args: Any, **_kwargs: Any) -> object:
            nonlocal connected
            connected = True
            return object()

    monkeypatch.setattr(worker_main, "preflight_answer_evidence_ocr", failed_preflight)
    monkeypatch.setattr(worker_main, "Client", FakeClient)
    monkeypatch.setattr(worker_main, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(worker_main, "configure_tracing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(
            log_level="INFO",
            temporal_address="temporal:7233",
            temporal_namespace="default",
            temporal_task_queue="collection-test",
        ),
    )

    with pytest.raises(RuntimeError, match="broken OCR runtime"):
        await worker_main.run_worker()

    assert connected is False
