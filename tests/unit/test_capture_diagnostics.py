from workflows.activities.capture_diagnostics import (
    capture_failure_code,
    incomplete_stream_message,
    stream_failure_diagnostics,
)


def test_connection_failure_is_not_reported_as_timeout() -> None:
    message = incomplete_stream_message(
        {"failed": True, "elapsed_ms": 46000, "finished": False, "bytes_received": 120}
    )
    assert message.startswith("stream-connection-failed:")
    assert "elapsed_ms=46000" in message
    assert "failed=True" in message
    assert "bytes_received=120" in message
    assert capture_failure_code(RuntimeError(message)) == "stream_connection_failed"
    assert stream_failure_diagnostics(message) == {
        "elapsed_ms": 46000,
        "bytes_received": 120,
        "failed": True,
        "finished": False,
    }


def test_open_stream_timeout_retains_observed_timing() -> None:
    message = incomplete_stream_message({"failed": False, "elapsed_ms": 600000})
    assert message.startswith("stream-open-at-timeout:")
    assert "elapsed_ms=600000" in message


def test_failed_old_segment_does_not_misclassify_new_open_segment() -> None:
    message = incomplete_stream_message(
        {"failed": True, "terminal_failed": False, "elapsed_ms": 600000}
    )
    assert message.startswith("stream-open-at-timeout:")
