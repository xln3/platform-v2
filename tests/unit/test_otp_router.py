"""OTP 收件端点（api/geo_platform/otp）+ tools/otp_wait.py 单元测试。

全 fake：不起服务（TestClient 进程内）、不写真收件箱（monkeypatch
``GEO_OTP_INBOX_DIR`` → tmp_path）、两个 token env 一律 monkeypatch。
契约对齐旧 server/geosys/otp_ingest.py（已随旧系统 2026-08-07 退役归档）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from geo_platform.main import app
from geo_platform.otp import router as otp_router

from tools import otp_wait

PHONE = "13121622231"
SLOT = f"SIM1_中国联通_+86{PHONE}"
SMS = "【豆包】你的验证码 458213，5分钟内有效。"
CODE = "458213"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _otp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每用例隔离：收件箱指 tmp、注册表指 tmp、双 token 配好、频控桶清空。"""
    monkeypatch.setenv("GEO_OTP_INBOX_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_OTP_REGISTRY_PATH", str(tmp_path / "reg" / "registered.json"))
    monkeypatch.setenv("GEO_OTP_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "operator-secret")
    monkeypatch.setenv("GEO_ENV", "development")
    monkeypatch.setenv("GEO_OTP_APK_PATH", str(tmp_path / "missing.apk"))
    monkeypatch.delenv("GEO_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("GEO_OTP_APK_SHA256", raising=False)
    monkeypatch.delenv("GEO_OTP_APK_VERSION", raising=False)
    monkeypatch.delenv("GEO_OTP_APK_SIGNER_SHA256", raising=False)
    with otp_router._rate_lock:
        otp_router._rate_buckets.clear()
    yield


def _push(
    body: object,
    *,
    token: str = "relay-secret",
    content_type: str = "application/json",
    query: str = "",
) -> httpx.Response:
    payload = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
    return client.post(
        f"/api/v2/otp/push{query}",
        content=payload,
        headers={"X-Relay-Token": token, "Content-Type": content_type},
    )


def _latest(
    phone: str = PHONE, *, token: str = "operator-secret", within: str = "180"
) -> httpx.Response:
    return client.get(
        f"/api/v2/otp/latest?phone={phone}&within={within}", headers={"X-Operator-Token": token}
    )


# ── push：token 门 ─────────────────────────────────────────────────────────────


def test_push_relay_token_not_configured_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_OTP_RELAY_TOKEN", raising=False)
    resp = _push({"slot": SLOT, "sms": SMS})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_relay_disabled"


def test_push_relay_token_wrong_401() -> None:
    assert _push({"slot": SLOT, "sms": SMS}, token="wrong").status_code == 401


def test_push_relay_token_missing_401() -> None:
    resp = client.post("/api/v2/otp/push", content=json.dumps({"slot": SLOT, "sms": SMS}))
    assert resp.status_code == 401


# ── push：JSON slot 模板正常路由落盘（schema 逐字段对齐旧版） ────────────────────


def test_push_json_slot_template_routed_and_schema(tmp_path: Path) -> None:
    before = time.time()
    resp = _push({"slot": SLOT, "sms": SMS})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "have_code": True,
        "code_len": 6,
        "phone": "131***2231",
        "routed": True,
        "platform": "豆包",
    }
    files = list(Path(tmp_path).glob("*.json"))
    assert [p.name for p in files] == [f"{PHONE}.json"]
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    # 与旧版逐字段一致：{ts, phone, code, raw, from, platform, meta}
    assert set(rec) == {"ts", "phone", "code", "raw", "from", "platform", "meta"}
    assert rec["phone"] == PHONE
    assert rec["code"] == CODE
    assert rec["raw"] == SMS
    assert rec["from"] == ""
    assert rec["platform"] == "豆包"
    assert rec["meta"]["sim_slot"] == SLOT
    assert rec["meta"]["extract_method"] == "regex"
    assert before <= rec["ts"] <= time.time()  # ts = 服务端 epoch
    # append-only JSONL 台账
    event = json.loads(
        (Path(tmp_path) / "otp_events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert event["phone"] == PHONE and event["code"] == CODE
    assert event["platform"] == "豆包" and event["code_source"] == "extracted"
    assert "raw" not in event  # 台账不留原文（同旧链缺省口径）


def test_push_ios_shortcuts_json_routes_fixed_phone(tmp_path: Path) -> None:
    """Apple 快捷指令免费路径：固定 phone + 短信输入变量，无 Android 卡槽字段。"""
    resp = _push({"phone": PHONE, "sms": SMS})

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "have_code": True,
        "code_len": 6,
        "phone": "131***2231",
        "routed": True,
        "platform": "豆包",
    }
    record = json.loads((tmp_path / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert record["phone"] == PHONE
    assert record["raw"] == SMS
    assert record["code"] == CODE
    assert record["meta"] == {"extract_method": "regex"}


def test_push_form_urlencoded(tmp_path: Path) -> None:
    form = urllib.parse.urlencode({"slot": SLOT, "sms": SMS})
    resp = _push(form, content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 200
    assert resp.json()["routed"] is True and resp.json()["have_code"] is True
    assert (Path(tmp_path) / f"{PHONE}.json").exists()


# ── push：双卡错标被 slot 反解纠偏 ──────────────────────────────────────────────


def test_push_slot_overrides_mislabeled_body_phone(tmp_path: Path) -> None:
    wrong = "15510162660"
    resp = _push({"phone": wrong, "slot": f"SIM2_+86{PHONE}", "sms": SMS})
    assert resp.status_code == 200
    assert resp.json()["phone"] == "131***2231"  # 卡槽备注（SIM 硬件级真值）权威覆盖
    assert (Path(tmp_path) / f"{PHONE}.json").exists()
    assert not (Path(tmp_path) / f"{wrong}.json").exists()


# ── push：unrouted 软收（不丢码） ───────────────────────────────────────────────


def test_push_unrouted_soft_accept(tmp_path: Path) -> None:
    resp = _push({"sms": SMS})  # slot/body/URL 均无手机号
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["routed"] is False
    assert body["have_code"] is True and body["code_len"] == 6
    rec = json.loads((Path(tmp_path) / "unrouted.json").read_text(encoding="utf-8"))
    assert rec["phone"] == "unrouted" and rec["code"] == CODE


# ── push：text/plain 旧模板兼容 ─────────────────────────────────────────────────


def test_push_text_plain_legacy_template(tmp_path: Path) -> None:
    resp = _push(f"{PHONE}收到{SMS}", content_type="text/plain; charset=utf-8")
    assert resp.status_code == 200
    body = resp.json()
    assert body["routed"] is True and body["have_code"] is True
    assert body["platform"] == "豆包"
    rec = json.loads((Path(tmp_path) / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert rec["code"] == CODE and rec["raw"] == SMS  # raw 不含路由前缀「手机号收到」


def test_push_text_plain_urlencoded_body_decoded(tmp_path: Path) -> None:
    # SmsForwarder form 通道会把中文 URL 编码，hex 尾字节混进码旁会废掉抽码（旧链 live 教训）
    flat = urllib.parse.quote(f"{PHONE}收到{SMS}")
    resp = _push(flat, content_type="text/plain; charset=utf-8")
    assert resp.status_code == 200
    assert resp.json()["have_code"] is True


# ── push：抽码（6 位数字边界；绝不从更长数字串里切 6 位） ────────────────────────


def test_push_extract_six_digit_boundary_never_slices(tmp_path: Path) -> None:
    # 无关键词的 7 位数字（订单号）→ 拒抽（绝不切出 6 位）
    resp = _push({"slot": SLOT, "sms": "您的订单1234567已发货，请注意查收。"})
    assert resp.json()["have_code"] is False
    # 有关键词时 6 位真码胜，7 位订单号不被切也不抢位
    resp = _push({"slot": SLOT, "sms": "验证码123456，订单号7654321"})
    assert resp.json()["code_len"] == 6
    rec = json.loads((Path(tmp_path) / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert rec["code"] == "123456"


def test_push_hint_boundary_guard(tmp_path: Path) -> None:
    # 转发器 hint 必须作为独立数字串真出现在正文（数字边界），否则兜底也不采纳
    resp = _push({"slot": SLOT, "sms": "单号1234567已生成", "code": "123456"})
    assert resp.json()["have_code"] is False
    # 关键词被反诈样板污染（正则拒抽）但 hint 被正文数字边界佐证 → 采纳
    resp = _push({"slot": SLOT, "sms": "验证码请勿外泄，暗号 123456。", "code": "123456"})
    assert resp.json()["have_code"] is True
    rec = json.loads((Path(tmp_path) / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert rec["code"] == "123456" and rec["meta"]["extract_method"] == "hint"


def test_push_voice_channel(tmp_path: Path) -> None:
    # T-39 语音通道：显式 code_source='voice' + 人工听写 code，跳过短信抽码
    resp = _push({"slot": SLOT, "sms": "", "code": "889900", "code_source": "voice"})
    assert resp.json()["have_code"] is True
    rec = json.loads((Path(tmp_path) / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert rec["code"] == "889900" and rec["meta"]["extract_method"] == "voice"
    # 非 4-8 位数字的听写码 → 拒收
    resp = _push({"slot": SLOT, "sms": "", "code": "12", "code_source": "voice"})
    assert resp.json()["have_code"] is False


# ── push：平台【品牌】识别 / 无码仍落盘 / 原子写 ────────────────────────────────


def test_push_platform_brand_prefix() -> None:
    assert _push({"slot": SLOT, "sms": SMS}).json()["platform"] == "豆包"
    assert _push({"slot": SLOT, "sms": "【网易新闻】验证码778899"}).json()["platform"] == "网易新闻"
    # 无【品牌】前缀 → 空串；URL ?platform= 显式覆盖（每平台单独转发规则用）
    assert _push({"slot": SLOT, "sms": "代码 778899"}).json()["platform"] == ""
    resp = _push({"slot": SLOT, "sms": "代码 778899"}, query="?platform=deepseek")
    assert resp.json()["platform"] == "deepseek"


def test_push_unknown_platform_is_quarantined_without_dropping_sms(tmp_path: Path) -> None:
    """Untrusted labels are discarded, but a valid OTP push must still be accepted."""
    for platform in ('<img src=x onerror="alert(1)">', "unknown-brand", "豆包\u0000evil"):
        resp = _push({"slot": SLOT, "sms": "验证码 778899", "platform": platform})
        assert resp.status_code == 200
        assert resp.json()["platform"] == ""
        record = json.loads((tmp_path / f"{PHONE}.json").read_text(encoding="utf-8"))
        assert record["code"] == "778899"
        assert record["platform"] == ""


def test_push_platform_enum_accepts_known_case_alias() -> None:
    resp = _push({"slot": SLOT, "sms": "验证码 778899", "platform": "DeepSeek"})
    assert resp.status_code == 200
    assert resp.json()["platform"] == "deepseek"


def test_push_no_code_still_written(tmp_path: Path) -> None:
    resp = _push({"slot": SLOT, "sms": "【豆包】请勿泄露验证码，谨防诈骗。"})
    body = resp.json()
    assert body["ok"] is True and body["have_code"] is False and body["code_len"] == 0
    rec = json.loads((Path(tmp_path) / f"{PHONE}.json").read_text(encoding="utf-8"))
    assert rec["code"] == "" and rec["platform"] == "豆包"


def test_push_atomic_write_no_tmp_residue(tmp_path: Path) -> None:
    _push({"slot": SLOT, "sms": SMS})
    names = [p.name for p in Path(tmp_path).iterdir()]
    assert not any(n.endswith(".tmp") for n in names)


# ── push：响应/日志无秘密泄漏 ───────────────────────────────────────────────────


def test_push_response_never_leaks_code() -> None:
    resp = _push({"slot": SLOT, "sms": SMS})
    assert CODE not in resp.text
    assert PHONE not in resp.text  # phone 打码中间四位


# ── push：频控 ──────────────────────────────────────────────────────────────────


def test_push_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(otp_router._RATE_LIMITS, "push", (2, 60.0))
    assert _push({"slot": SLOT, "sms": SMS}).status_code == 200
    assert _push({"slot": SLOT, "sms": SMS}).status_code == 200
    assert _push({"slot": SLOT, "sms": SMS}).status_code == 429


# ── latest：operator 门 / within 窗 / 不存在 ────────────────────────────────────


def _write_inbox(
    tmp_path: Path, phone: str, *, ts: float, code: str = CODE, platform: str = "豆包"
) -> None:
    rec = {"ts": ts, "phone": phone, "code": code, "raw": SMS, "from": "", "platform": platform}
    (Path(tmp_path) / f"{phone}.json").write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8"
    )


def test_latest_operator_token_not_configured_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_OTP_OPERATOR_TOKEN", raising=False)
    resp = _latest()
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_operator_disabled"


def test_latest_operator_token_wrong_401() -> None:
    assert _latest(token="wrong").status_code == 401


def test_latest_bad_phone_400() -> None:
    assert _latest(phone="123").status_code == 400


def test_latest_found_within_window(tmp_path: Path) -> None:
    _write_inbox(tmp_path, PHONE, ts=time.time() - 30)
    resp = _latest()
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["found"] is True
    assert body["code"] == CODE and body["platform"] == "豆包"
    assert 29 <= body["age_s"] <= 90


def test_latest_stale_outside_window(tmp_path: Path) -> None:
    _write_inbox(tmp_path, PHONE, ts=time.time() - 400)
    body = _latest().json()
    assert body["found"] is False and body["reason"] == "stale"


def test_latest_no_code_extracted(tmp_path: Path) -> None:
    _write_inbox(tmp_path, PHONE, ts=time.time(), code="")
    body = _latest().json()
    assert body["found"] is False and body["reason"] == "no_code_extracted"


def test_latest_no_sms_for_phone() -> None:
    body = _latest().json()
    assert body["ok"] is True and body["found"] is False
    assert body["reason"] == "no_sms_for_phone"


def test_latest_within_clamped(tmp_path: Path) -> None:
    _write_inbox(tmp_path, PHONE, ts=time.time())
    assert _latest(within="99999").json()["found"] is True  # 硬夹 900 内仍新鲜
    body = client.get(
        f"/api/v2/otp/latest?phone={PHONE}&within=abc",
        headers={"X-Operator-Token": "operator-secret"},
    ).json()
    assert body["ok"] is True  # 畸形 within → 默认 180，绝不 422


# ── tools/otp_wait.py ───────────────────────────────────────────────────────────


def test_otp_wait_success_injected_fetcher() -> None:
    calls = []

    def fetch() -> str | None:
        calls.append(1)
        return CODE if len(calls) >= 3 else None

    code = otp_wait.wait_for_code(timeout_s=10, interval_s=2, fetch=fetch, sleep=lambda s: None)
    assert code == CODE and len(calls) == 3


def test_otp_wait_timeout_fake_clock() -> None:
    now = [1000.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += s

    result = otp_wait.wait_for_code(
        timeout_s=5, interval_s=2, fetch=lambda: None, sleep=sleep, clock=clock
    )
    assert result is None and now[0] >= 1005.0


def test_otp_wait_main_missing_token_exit3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_OTP_OPERATOR_TOKEN", raising=False)
    assert otp_wait.main(["--phone", PHONE]) == 3


def test_otp_wait_main_bad_phone_exit3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "tok")
    assert otp_wait.main(["--phone", "123"]) == 3


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    """按队列返回响应的 fake httpx.Client（绝不真发网络）。"""

    queue: list[object] = []
    last_params: dict[str, object] = {}
    init_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeClient.init_kwargs = kwargs

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        _FakeClient.last_params = dict(kwargs.get("params") or {})  # type: ignore[arg-type]
        item = _FakeClient.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, _FakeResponse)
        return item


def test_otp_wait_make_fetcher_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otp_wait.httpx, "Client", _FakeClient)
    _FakeClient.queue = [_FakeResponse(200, {"ok": True, "found": True, "code": CODE})]
    fetch = otp_wait.make_fetcher(
        base="https://127.0.0.1:8443", token="tok", phone=PHONE, within=180
    )
    assert fetch() == CODE
    assert _FakeClient.last_params == {"phone": PHONE, "within": 180}
    assert _FakeClient.init_kwargs.get("trust_env") is False
    assert _FakeClient.init_kwargs.get("verify") is False


def test_otp_wait_make_fetcher_503_raises_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otp_wait.httpx, "Client", _FakeClient)
    _FakeClient.queue = [_FakeResponse(503, {"error": "otp_operator_disabled"})]
    fetch = otp_wait.make_fetcher(base="https://x", token="tok", phone=PHONE, within=180)
    with pytest.raises(otp_wait.OtpWaitConfigError):
        fetch()


def test_otp_wait_make_fetcher_network_error_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(otp_wait.httpx, "Client", _FakeClient)
    _FakeClient.queue = [
        httpx.ConnectError("boom"),
        _FakeResponse(200, {"ok": True, "found": False}),
        _FakeResponse(200, {"ok": True, "found": True, "code": CODE}),
    ]
    fetch = otp_wait.make_fetcher(base="https://x", token="tok", phone=PHONE, within=180)
    assert fetch() is None  # 网络错误 → 当无码重试
    assert fetch() is None
    assert fetch() == CODE


def test_otp_wait_main_end_to_end_exit0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "tok")
    monkeypatch.setattr(otp_wait.httpx, "Client", _FakeClient)
    _FakeClient.queue = [_FakeResponse(200, {"ok": True, "found": True, "code": CODE})]
    assert otp_wait.main(["--phone", PHONE, "--timeout", "5"]) == 0
    assert capsys.readouterr().out.strip() == CODE  # 码只进 stdout


def test_otp_wait_main_401_exit3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "bad-tok")
    monkeypatch.setattr(otp_wait.httpx, "Client", _FakeClient)
    _FakeClient.queue = [_FakeResponse(401)]
    assert otp_wait.main(["--phone", PHONE, "--timeout", "5"]) == 3


def test_otp_wait_strip_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:7890")
    otp_wait.strip_proxy_env()
    assert not any(
        k in os.environ
        for k in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        )
    )


# ── 装机配置页 / setup-info / status / apk ─────────────────────────────────────


def test_setup_page_public_and_secret_free() -> None:
    """配置页公开可达，但 HTML 本体绝不内嵌任何秘密（relay/operator token）。"""
    resp = client.get("/api/v2/otp/setup")
    assert resp.status_code == 200
    html = resp.text
    assert "setup-info" in html  # 解锁后经受门端点拉配置
    assert "relay-secret" not in html and "operator-secret" not in html
    assert "73mOY3" not in html  # 生产 token 片段同样不得出现
    assert resp.headers["cache-control"] == "private, no-store"


def test_setup_page_has_nonce_csp_and_no_html_injection_sinks() -> None:
    resp = client.get("/api/v2/otp/setup")
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp)
    assert nonce is not None
    assert f'<script nonce="{nonce.group(1)}">' in resp.text
    assert f'<style nonce="{nonce.group(1)}">' in resp.text
    assert "__CSP_NONCE__" not in resp.text
    assert "innerHTML" not in resp.text
    assert "style=" not in resp.text


def test_setup_page_has_short_lived_operator_session_and_honest_copy_feedback() -> None:
    html = client.get("/api/v2/otp/setup").text
    assert "IDLE_LOCK_MS = 20 * 60 * 1000" in html
    assert "闲置 20 分钟自动锁定" in html
    assert 'window.addEventListener("pagehide"' in html
    assert "event.persisted" in html
    assert 'cache: "no-store"' in html
    assert 'credentials: "omit"' in html
    assert "localStorage" not in html and "sessionStorage" not in html
    assert 'copied ? "已复制" : "复制失败，请手动选择"' in html
    assert "请点击下方复制按钮" in html  # async 注册完成后不假装仍有剪贴板激活
    assert 'download="SmsForwarder.apk"' in html  # 下载 APK 不应导航离开并触发重新鉴权
    assert 'bindGate("iosUrl", "cpIosUrl", payload.push_url)' in html
    assert 'bindGate("iosTok", "cpIosTok", payload.relay_token)' in html
    assert '["iosUrl", "cpIosUrl"]' in html and '["iosTok", "cpIosTok"]' in html


def test_setup_page_has_free_ios_shortcuts_workflow() -> None:
    html = client.get("/api/v2/otp/setup").text

    assert "iPhone · 免费转发（Apple 快捷指令）" in html
    assert "无需下载第三方转发器" in html
    assert "无订阅费" in html
    assert "立即运行" in html and "运行前询问" in html
    assert "获取 URL 内容" in html and "请求正文选 <b>JSON</b>" in html
    assert "X-Relay-Token" in html
    assert "快捷指令输入" in html
    assert "phone</span> = 刚刚注册的 11 位接码号码" in html
    assert "一台 iPhone 只配置一个接码号码" in html
    assert "当前入口使用系统信任的公共 CA 证书" in html
    assert "不需要安装自签证书或描述文件" in html
    assert "不要改用 HTTP 或关闭校验" in html
    assert "当前自签证书" not in html
    assert "iPhone/iOS 无法安装，也不能按本页流程" not in html


def test_setup_page_has_accessible_device_switch_and_no_registry_listing() -> None:
    html = client.get("/api/v2/otp/setup").text

    assert 'role="group" aria-label="选择短信转发手机系统"' in html
    assert 'id="deviceIos" aria-pressed="true" aria-controls="iosGuide"' in html
    assert 'id="deviceAndroid" aria-pressed="false"' in html
    assert 'id="iosGuide" aria-labelledby="iphone"' in html
    assert 'id="androidGuide" aria-labelledby="android" hidden' in html
    assert 'selectDevice("ios")' in html and 'selectDevice("android")' in html
    assert 'byId("iosGuide").hidden = !iosSelected' in html
    assert 'byId("androidGuide").hidden = iosSelected' in html

    assert "本页不显示或枚举在册号码" in html
    assert 'id="btnSlots"' not in html
    assert 'id="slots"' not in html
    assert "fillSlots" not in html
    assert "刷新获取在册号码" not in html


def test_setup_info_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """operator 门：env 未配 503、错 token 401、正 token 200 且含 relay token。"""
    monkeypatch.delenv("GEO_OTP_OPERATOR_TOKEN", raising=False)
    assert client.get("/api/v2/otp/setup-info").status_code == 503
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "operator-secret")
    assert (
        client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "wrong"}).status_code
        == 401
    )
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["relay_token"] == "relay-secret"
    assert data["push_url"].endswith("/api/v2/otp/push")
    assert data["body_template"] == '{"slot":"{{CARD_SLOT}}","sms":"{{SMS}}"}'
    assert "豆包" in data["whitelist_regex"]
    assert data["whitelist_regex"].startswith("(?is)^")
    assert "验证码" in data["whitelist_regex"]
    assert "|腾讯|" not in data["whitelist_regex"]
    rule = re.compile(data["whitelist_regex"])
    assert rule.fullmatch("【豆包】您的验证码为 123456")
    assert rule.fullmatch("【百度】验证码 123456")  # 现役文心短信签名，但不能单独放行
    assert not rule.fullmatch("【百度】地图行程已经开始")
    assert not rule.fullmatch("【腾讯】支付验证码 123456")
    assert not rule.fullmatch("【豆包】新品营销活动")
    assert not rule.fullmatch("这条非平台短信提到豆包验证码 123456")
    assert "slot_remarks" not in data
    assert "13121622231" not in resp.text


def test_setup_info_uses_explicit_public_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_PUBLIC_BASE_URL", "https://39.105.175.14:8443/")
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["push_url"] == "https://39.105.175.14:8443/api/v2/otp/push"
    assert data["apk_url"] == "https://39.105.175.14:8443/api/v2/otp/smsforwarder.apk"
    assert "latest_example" not in data


@pytest.mark.parametrize(
    "public_base",
    (
        "http://public.example:8443",
        "https://user:secret@example.test",
        "https://example.test/path",
        "https://example.test/?token=secret",
        "https://example.test:99999",
        "https://bad host.example",
    ),
)
def test_setup_info_rejects_unsafe_public_base(
    monkeypatch: pytest.MonkeyPatch, public_base: str
) -> None:
    monkeypatch.setenv("GEO_PUBLIC_BASE_URL", public_base)
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_public_base_invalid"
    assert resp.headers["cache-control"] == "private, no-store"


def test_setup_info_requires_public_base_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_ENV", "production")
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_public_base_missing"


def test_operator_responses_are_never_cacheable(tmp_path: Path) -> None:
    _write_inbox(tmp_path, PHONE, ts=time.time())
    responses = (
        _latest(),
        client.get("/api/v2/otp/status", headers={"X-Operator-Token": "operator-secret"}),
        client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"}),
        _register({"phone": NEW_PHONE}),
        client.get("/api/v2/otp/status", headers={"X-Operator-Token": "wrong"}),
    )
    for response in responses:
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["expires"] == "0"


def test_latest_poll_does_not_duplicate_success_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LogSpy:
        def __init__(self) -> None:
            self.info_events: list[str] = []
            self.warning_events: list[str] = []

        def info(self, event: str, **_fields: object) -> None:
            self.info_events.append(event)

        def warning(self, event: str, **_fields: object) -> None:
            self.warning_events.append(event)

    spy = LogSpy()
    monkeypatch.setattr(otp_router, "log", spy)

    assert _latest().status_code == 200
    assert "otp_operator_access" not in spy.info_events

    status = client.get("/api/v2/otp/status", headers={"X-Operator-Token": "operator-secret"})
    assert status.status_code == 200
    assert spy.info_events == ["otp_operator_access"]

    assert _latest(token="wrong").status_code == 401
    assert spy.warning_events == ["otp_operator_auth_failed"]


def test_setup_info_never_exposes_registered_or_env_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装机配置只下发通道参数，不能枚举注册表或历史 env 号码。"""
    monkeypatch.setenv(
        "GEO_OTP_SLOT_REMARKS", "SIM1_中国移动_+8613900001111, SIM2_联通_+8613900002222"
    )
    assert _register({"phone": NEW_PHONE, "slot": "eSIM"}).status_code == 200
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    assert "slot_remarks" not in resp.json()
    assert NEW_PHONE not in resp.text
    assert "13900001111" not in resp.text


# ── register：在册号码注册入口（卡槽自由文本，不限 SIM1/2） ──────────────────────


NEW_PHONE = "13912345678"


def _register(body: object, *, token: str = "operator-secret") -> httpx.Response:
    return client.post(
        "/api/v2/otp/register",
        content=json.dumps(body, ensure_ascii=False),
        headers={"X-Operator-Token": token, "Content-Type": "application/json"},
    )


def test_register_operator_token_not_configured_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEO_OTP_OPERATOR_TOKEN", raising=False)
    resp = _register({"phone": NEW_PHONE})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "otp_operator_disabled"


def test_register_operator_token_wrong_401() -> None:
    assert _register({"phone": NEW_PHONE}, token="wrong").status_code == 401
    assert (
        client.post("/api/v2/otp/register", content=json.dumps({"phone": NEW_PHONE})).status_code
        == 401
    )


def test_register_bad_phone_400() -> None:
    assert _register({"phone": "123"}).status_code == 400
    assert _register({"phone": ""}).status_code == 400
    assert _register("not-json").status_code == 400


def test_register_persists_without_becoming_enumerable_in_setup_info(tmp_path: Path) -> None:
    resp = _register({"phone": NEW_PHONE, "carrier": "中国联通", "slot": "SIM1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["created"] is True
    assert body["remark"] == f"SIM1_中国联通_+86{NEW_PHONE}"
    assert body["phone"] == "139***5678"  # phone 字段掩码（remark 是 operator 自填自拿）
    # 注册表落盘（原子写，无 .tmp 残留）
    entries = json.loads((tmp_path / "reg" / "registered.json").read_text(encoding="utf-8"))
    assert [e["phone"] for e in entries] == [NEW_PHONE]
    assert entries[0]["remark"] == body["remark"]
    assert not any(p.name.endswith(".tmp") for p in (tmp_path / "reg").iterdir())
    # setup-info 不再下发整张注册表；刚提交的 remark 只存在本次 POST 响应。
    setup = client.get(
        "/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"}
    )
    assert setup.status_code == 200
    assert "slot_remarks" not in setup.json()
    assert NEW_PHONE not in setup.text
    # 注册备注可被卡槽反解回真号（路由契约不破）
    assert otp_router.phone_from_slot(body["remark"]) == NEW_PHONE


def test_register_slot_freeform_not_limited_to_sim12() -> None:
    """卡槽是选填自由文本：eSIM/自定义标签/留空全合法（注册绝不限制 SIM1/2）。"""
    resp = _register({"phone": NEW_PHONE, "carrier": "中国电信", "slot": "eSIM"})
    assert resp.status_code == 200
    assert resp.json()["remark"] == f"eSIM_中国电信_+86{NEW_PHONE}"
    resp = _register({"phone": "13800001111"})
    assert resp.status_code == 200
    assert resp.json()["remark"] == "+8613800001111"  # 槽位/运营商全空也合法
    assert otp_router.phone_from_slot(resp.json()["remark"]) == "13800001111"
    # 带 +86 前缀的输入被归一
    resp = _register({"phone": "+8613800002222", "slot": "卡2"})
    assert resp.status_code == 200
    assert resp.json()["remark"] == "卡2_+8613800002222"


def test_register_upsert_same_phone(tmp_path: Path) -> None:
    """同号再注册=更新备注（注册表按 phone 唯一，绝不出重复号）。"""
    assert _register({"phone": NEW_PHONE, "slot": "SIM1"}).json()["created"] is True
    resp = _register({"phone": NEW_PHONE, "slot": "SIM2", "carrier": "中国移动"})
    assert resp.status_code == 200 and resp.json()["created"] is False
    entries = json.loads((tmp_path / "reg" / "registered.json").read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["remark"] == f"SIM2_中国移动_+86{NEW_PHONE}"


def test_register_does_not_reintroduce_env_or_registry_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GEO_OTP_SLOT_REMARKS", f"SIM1_中国移动_+86{NEW_PHONE}, SIM2_联通_+8613900002222"
    )
    _register({"phone": NEW_PHONE, "slot": "eSIM", "carrier": "中国电信"})
    response = client.get(
        "/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"}
    )
    assert response.status_code == 200
    assert "slot_remarks" not in response.json()
    assert NEW_PHONE not in response.text
    assert "13900002222" not in response.text


def test_setup_info_registry_corrupt_best_effort(tmp_path: Path) -> None:
    """setup-info 不读取注册表，因此损坏文件既不泄露也不影响通道配置。"""
    reg = tmp_path / "reg" / "registered.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("{broken", encoding="utf-8")
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    assert "slot_remarks" not in resp.json()


def test_setup_page_register_form_no_sim_restriction() -> None:
    """通用注册入口走受门端点，卡槽是自由文本（页面不再内嵌 SIM 二选一）。"""
    html = client.get("/api/v2/otp/setup").text
    assert "/api/v2/otp/register" in html
    assert 'id="simSel"' not in html  # 旧 SIM1/SIM2 下拉已移除


def test_setup_page_channel_rule_mirror_app_form() -> None:
    """第 3/4 步按 SmsForwarder v3.5.0 实际表单逐格给出（20260810 真机截图校准）。"""
    html = client.get("/api/v2/otp/setup").text
    # Webhook 通道表单字段逐格对应（文本框复制项 + 单选/留空指示）
    for frag in (
        "通道名称/状态",
        "请求方式",
        "Webhook Server",
        "消息模板",
        "Secret",
        "成功应答关键字",
        "Headers 第 1 行 Key",
        "Headers 第 1 行 Value",
        "Headers 第 2 行",
        "代理设置",
    ):
        assert frag in html, frag
    # 转发规则表单字段逐格对应
    for frag in (
        "规则别名",
        "发送通道",
        "匹配卡槽",
        "匹配字段",
        "匹配模式",
        "匹配的值",
        "不限卡槽",
        "启用自定义模版",
        "启用该条转发规则",
        "免打扰",
    ):
        assert frag in html, frag
    # 第 0 步必查项：全局免打扰=00:00~00:00（20260810 实测 00:00~24:00 全天禁转发案例）
    assert "全天禁转发" in html
    # 推送地址只采用 setup-info 的显式公网基址契约，不再让浏览器/代理各自猜端口。
    assert 'bindGate("vUrl", "cpUrl", payload.push_url)' in html
    assert "location.origin" not in html
    # v3.5.0 无「忽略 SSL 证书」开关（自托管 APK strings 实证），旧指令不得再出现
    # （页面保留一句「该开关不存在」的废止说明是有意的——旧 SmsForwarder 文档仍写着要勾）
    assert "必须勾选" not in html
    # 受门复制按钮初始 disabled 且无 data-t，静态绑定器按 .cp[data-t] 选择——
    # 解锁前点击绝不可能把 "null" 复制进剪贴板
    assert 'id="vUrl"' in html and 'id="cpTok"' in html
    assert 'querySelectorAll(".cp[data-t]")' in html
    assert "平台签名 + 验证码业务词" in html
    assert "iPhone 快捷指令 / Android SmsForwarder" in html
    assert "选择：苹果自带「快捷指令」" in html
    assert "当前显示 Android SmsForwarder 方案" in html
    assert "公网入口应由系统信任的公共 CA 正常验证" in html
    assert "不要绕过警告" in html
    assert "文件哈希一致只证明拿到既定 APK，不代表手机传输已安全" in html


def test_status_masked_and_gated(tmp_path: Path) -> None:
    """status：operator 门 + 全掩码（无 code 无原文）。"""
    assert client.get("/api/v2/otp/status").status_code == 401
    _push({"slot": SLOT, "sms": SMS})
    resp = client.get("/api/v2/otp/status", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    rows = resp.json()["recent"]
    assert rows and rows[0]["phone"] == "131***2231"
    assert rows[0]["code_len"] == 6 and rows[0]["platform"] == "豆包"
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", rows[0]["time"])  # 绝对时间戳
    assert CODE not in resp.text and "458213" not in resp.text  # 码绝不出现在 status


def test_status_quarantines_legacy_unknown_platform(tmp_path: Path) -> None:
    """Pre-fix inbox files cannot reintroduce attacker-controlled markup into status."""
    _write_inbox(
        tmp_path,
        PHONE,
        ts=time.time(),
        platform='<img src=x onerror="globalThis.pwned=true">',
    )
    resp = client.get("/api/v2/otp/status", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    assert resp.json()["recent"][0]["platform"] == "-"
    assert "onerror" not in resp.text


def test_apk_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """APK 公开下载：文件缺失 404，存在则 200 + 安卓包 MIME。"""
    monkeypatch.setenv("GEO_OTP_APK_PATH", str(tmp_path / "nope.apk"))
    assert client.get("/api/v2/otp/smsforwarder.apk").status_code == 404
    apk = tmp_path / "SmsForwarder.apk"
    apk.write_bytes(b"PK\x03\x04-fake-apk")
    monkeypatch.setenv("GEO_OTP_APK_PATH", str(apk))
    resp = client.get("/api/v2/otp/smsforwarder.apk")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.android.package-archive"
    assert resp.content == b"PK\x03\x04-fake-apk"
    assert resp.headers["x-apk-sha256"] == hashlib.sha256(resp.content).hexdigest()
    assert resp.headers["cache-control"] == "public, max-age=300, must-revalidate"


def test_apk_readiness_and_integrity_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "SmsForwarder.apk"
    apk.write_bytes(b"PK\x03\x04-release-apk")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    monkeypatch.setenv("GEO_OTP_APK_PATH", str(apk))
    monkeypatch.setenv("GEO_OTP_APK_SHA256", digest)
    monkeypatch.setenv("GEO_OTP_APK_VERSION", "3.5.0.260224")
    monkeypatch.setenv("GEO_OTP_APK_SIGNER_SHA256", "AA:" * 31 + "AA")

    info = client.get("/api/v2/otp/apk-info")
    assert info.status_code == 200
    state = info.json()["apk"]
    assert state["ready"] is True and state["integrity"] == "verified"
    assert state["sha256"] == digest and state["size_bytes"] == apk.stat().st_size
    assert state["version"] == "3.5.0.260224"
    assert "GEO_OTP_APK_PATH" not in info.text and str(tmp_path) not in info.text

    download = client.get("/api/v2/otp/smsforwarder.apk")
    assert download.status_code == 200
    assert download.headers["x-apk-sha256"] == digest


def test_apk_hash_mismatch_disables_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "SmsForwarder.apk"
    apk.write_bytes(b"PK\x03\x04-tampered")
    monkeypatch.setenv("GEO_OTP_APK_PATH", str(apk))
    monkeypatch.setenv("GEO_OTP_APK_SHA256", "0" * 64)

    state = client.get("/api/v2/otp/apk-info").json()["apk"]
    assert state["ready"] is False
    assert state["reason"] == "apk_integrity_failed"
    response = client.get("/api/v2/otp/smsforwarder.apk")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "apk_integrity_failed"
    assert b"tampered" not in response.content


def test_smsforwarder_license_notice_is_distributed_with_apk() -> None:
    response = client.get("/api/v2/otp/smsforwarder-license")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "BSD 2-Clause License" in response.text
    assert "Copyright (c) 2021, pppscn" in response.text
    page = client.get("/api/v2/otp/setup").text
    assert "/api/v2/otp/smsforwarder-license" in page
    assert "商业使用边界须由权利人或法务书面确认" in page
