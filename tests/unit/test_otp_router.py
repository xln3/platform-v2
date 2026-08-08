"""OTP 收件端点（api/geo_platform/otp）+ tools/otp_wait.py 单元测试。

全 fake：不起服务（TestClient 进程内）、不写真收件箱（monkeypatch
``GEO_OTP_INBOX_DIR`` → tmp_path）、两个 token env 一律 monkeypatch。
契约对齐旧 server/geosys/otp_ingest.py（已随旧系统 2026-08-07 退役归档）。
"""

from __future__ import annotations

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
    """每用例隔离：收件箱指 tmp、双 token 配好、频控桶清空。"""
    monkeypatch.setenv("GEO_OTP_INBOX_DIR", str(tmp_path))
    monkeypatch.setenv("GEO_OTP_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "operator-secret")
    with otp_router._rate_lock:
        otp_router._rate_buckets.clear()
    yield


def _push(body: object, *, token: str = "relay-secret",
          content_type: str = "application/json", query: str = "") -> httpx.Response:
    payload = json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else str(body)
    return client.post(f"/api/v2/otp/push{query}", content=payload,
                       headers={"X-Relay-Token": token, "Content-Type": content_type})


def _latest(phone: str = PHONE, *, token: str = "operator-secret",
            within: str = "180") -> httpx.Response:
    return client.get(f"/api/v2/otp/latest?phone={phone}&within={within}",
                      headers={"X-Operator-Token": token})


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
    assert body == {"ok": True, "have_code": True, "code_len": 6,
                    "phone": "131***2231", "routed": True, "platform": "豆包"}
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
    event = json.loads((Path(tmp_path) / "otp_events.jsonl")
                       .read_text(encoding="utf-8").splitlines()[0])
    assert event["phone"] == PHONE and event["code"] == CODE
    assert event["platform"] == "豆包" and event["code_source"] == "extracted"
    assert "raw" not in event  # 台账不留原文（同旧链缺省口径）


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


def _write_inbox(tmp_path: Path, phone: str, *, ts: float, code: str = CODE,
                 platform: str = "豆包") -> None:
    rec = {"ts": ts, "phone": phone, "code": code, "raw": SMS, "from": "", "platform": platform}
    (Path(tmp_path) / f"{phone}.json").write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8")


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
    body = client.get(f"/api/v2/otp/latest?phone={PHONE}&within=abc",
                      headers={"X-Operator-Token": "operator-secret"}).json()
    assert body["ok"] is True  # 畸形 within → 默认 180，绝不 422


# ── tools/otp_wait.py ───────────────────────────────────────────────────────────


def test_otp_wait_success_injected_fetcher() -> None:
    calls = []

    def fetch() -> str | None:
        calls.append(1)
        return CODE if len(calls) >= 3 else None

    code = otp_wait.wait_for_code(timeout_s=10, interval_s=2, fetch=fetch,
                                  sleep=lambda s: None)
    assert code == CODE and len(calls) == 3


def test_otp_wait_timeout_fake_clock() -> None:
    now = [1000.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += s

    result = otp_wait.wait_for_code(timeout_s=5, interval_s=2,
                                    fetch=lambda: None, sleep=sleep, clock=clock)
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
    fetch = otp_wait.make_fetcher(base="https://127.0.0.1:8443", token="tok",
                                  phone=PHONE, within=180)
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
    _FakeClient.queue = [httpx.ConnectError("boom"),
                         _FakeResponse(200, {"ok": True, "found": False}),
                         _FakeResponse(200, {"ok": True, "found": True, "code": CODE})]
    fetch = otp_wait.make_fetcher(base="https://x", token="tok", phone=PHONE, within=180)
    assert fetch() is None  # 网络错误 → 当无码重试
    assert fetch() is None
    assert fetch() == CODE


def test_otp_wait_main_end_to_end_exit0(monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
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
    for key in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:7890")
    otp_wait.strip_proxy_env()
    assert not any(k in os.environ for k in (
        "http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))


# ── 装机配置页 / setup-info / status / apk ─────────────────────────────────────


def test_setup_page_public_and_secret_free() -> None:
    """配置页公开可达，但 HTML 本体绝不内嵌任何秘密（relay/operator token）。"""
    resp = client.get("/api/v2/otp/setup")
    assert resp.status_code == 200
    html = resp.text
    assert "setup-info" in html  # 解锁后经受门端点拉配置
    assert "relay-secret" not in html and "operator-secret" not in html
    assert "73mOY3" not in html  # 生产 token 片段同样不得出现


def test_setup_info_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """operator 门：env 未配 503、错 token 401、正 token 200 且含 relay token。"""
    monkeypatch.delenv("GEO_OTP_OPERATOR_TOKEN", raising=False)
    assert client.get("/api/v2/otp/setup-info").status_code == 503
    monkeypatch.setenv("GEO_OTP_OPERATOR_TOKEN", "operator-secret")
    assert client.get("/api/v2/otp/setup-info",
                      headers={"X-Operator-Token": "wrong"}).status_code == 401
    resp = client.get("/api/v2/otp/setup-info",
                      headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["relay_token"] == "relay-secret"
    assert data["push_url"].endswith("/api/v2/otp/push")
    assert data["body_template"] == '{"slot":"{{CARD_SLOT}}","sms":"{{SMS}}"}'
    assert "豆包" in data["whitelist_regex"] and data["whitelist_regex"].startswith("(?s).*")
    assert any("13121622231" in s for s in data["slot_remarks"])


def test_setup_info_slot_remarks_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """在册卡槽备注的真源是 env GEO_OTP_SLOT_REMARKS（换号改一处，页面刷新即得）。"""
    monkeypatch.setenv("GEO_OTP_SLOT_REMARKS",
                       "SIM1_中国移动_+8613900001111, SIM2_联通_+8613900002222")
    resp = client.get("/api/v2/otp/setup-info", headers={"X-Operator-Token": "operator-secret"})
    assert resp.status_code == 200
    assert resp.json()["slot_remarks"] == [
        "SIM1_中国移动_+8613900001111", "SIM2_联通_+8613900002222",
    ]


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
