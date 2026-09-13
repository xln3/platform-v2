from types import SimpleNamespace
from typing import Any

import pytest

from tools import activate_elastic_proxy as activation
from workflows.activities.region_proxy_router import ResolvedRegionProxy


def verified() -> ResolvedRegionProxy:
    return ResolvedRegionProxy(
        "http://new-proxy", "wukong", "310000", "310000", "上海", "bought", "310000"
    )


def install(
    monkeypatch: pytest.MonkeyPatch, *, busy: bool = False, fail_restart: bool = False
) -> list[str]:
    events: list[str] = []
    region = SimpleNamespace(relay_unit="geo-platform-v2-proxy-relay@sh.service")
    browser = SimpleNamespace(instance_key="doubao_sh", activity="idle")
    account = SimpleNamespace(
        runtime_state="running" if busy else "idle",
        current_run_pub_id="run-active" if busy else None,
    )

    class Session:
        def __init__(self, engine: Any) -> None:
            self.reads = 0

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def scalar(self, statement: Any) -> Any:
            return (
                SimpleNamespace(state="running") if "collection_run" in str(statement) else region
            )

        def scalars(self, statement: Any) -> Any:
            return [account] if "collection_platform_account" in str(statement) else [browser]

        def commit(self) -> None:
            events.append("commit")

    def command(args: list[str], **kwargs: Any) -> Any:
        action = args[1]
        events.append(action)
        if action == "restart" and fail_restart and events.count("restart") == 1:
            raise RuntimeError("relay restart failed")
        return SimpleNamespace(stdout="/etc/geo-platform-v2/proxy-relay-sh.env (ignore_errors=no)")

    def fence(*args: Any, **kwargs: Any) -> Any:
        events.append("fence")
        return SimpleNamespace(platform="doubao_sh", fencing_token=7)

    monkeypatch.setattr(
        activation, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(activation, "get_settings", lambda: SimpleNamespace(postgres_dsn="unused"))
    monkeypatch.setattr(activation, "Session", Session)
    monkeypatch.setattr(activation.subprocess, "run", command)
    monkeypatch.setattr(
        activation,
        "Path",
        lambda _: SimpleNamespace(read_text=lambda: "UPSTREAM_PROXY_URL=http://old-proxy\n"),
    )
    monkeypatch.setattr(activation, "_rewrite_env", lambda path, proxy, **kw: events.append(proxy))
    monkeypatch.setattr(activation, "unit_active", lambda _: True)
    monkeypatch.setattr(activation, "acquire_browser_fence", fence)
    monkeypatch.setattr(
        activation, "release_browser_fence", lambda *a, **kw: events.append("release")
    )
    return events


def test_busy_region_is_never_reconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    events = install(monkeypatch, busy=True)
    assert activation.activate_verified_proxy(verified()) == "waiting_for_region_drain"
    assert events == ["show"]


def test_activation_is_fenced_and_restores_browsers(monkeypatch: pytest.MonkeyPatch) -> None:
    events = install(monkeypatch)
    assert activation.activate_verified_proxy(verified()) == "activated"
    assert events == [
        "show",
        "fence",
        "stop",
        "http://new-proxy",
        "restart",
        "start",
        "release",
        "commit",
    ]


def test_relay_failure_restores_previous_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    events = install(monkeypatch, fail_restart=True)
    with pytest.raises(RuntimeError, match="relay restart failed"):
        activation.activate_verified_proxy(verified())
    assert events[-4:] == ["restart", "http://old-proxy", "restart", "start"]
    assert "commit" not in events


def test_wrong_region_fails_before_any_operations() -> None:
    proxy = verified()
    wrong = ResolvedRegionProxy(
        proxy.proxy_url,
        proxy.source,
        proxy.requested_region,
        proxy.region_gb,
        proxy.city,
        proxy.provider_action,
        "110000",
    )
    with pytest.raises(ValueError, match="region mismatch"):
        activation.activate_verified_proxy(wrong)
