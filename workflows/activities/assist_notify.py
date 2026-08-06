"""验证码人工接管推送网关（bark / serverchan / 企业微信 / ntfy / raw）。

纪律：
- 绝不抛异常——推送失败/对端异常只记 warning 返回 False，绝不阻断采集 workflow。
- env 不由本模块读取（``GEO_ASSIST_NOTIFY_FLAVOR`` / ``GEO_ASSIST_NOTIFY_URL``
  由调用方 captcha_assist 读后传入），便于测试与按会话覆盖。
- stdlib urllib + 硬超时，不引第三方依赖。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from email.header import Header

import structlog

log = structlog.get_logger()

# body 里含 assist_url（调用方拼装）；bark 的 ?url= / raw 的 url 字段从这里抽取。
_URL_RE = re.compile(r"https?://\S+")


def _extract_url(body: str) -> str:
    m = _URL_RE.search(body or "")
    return m.group(0) if m else ""


def _request(req: urllib.request.Request, timeout_s: float) -> bool:
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return 200 <= resp.status < 300


def _json_post(url: str, payload: dict) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def _ntfy_title(title: str) -> str:
    """ntfy 要求非 ASCII 标题走 RFC 2047 encoded-word（urllib 也只收 latin-1 头）。"""
    if all(ord(c) < 128 for c in title):
        return title
    return Header(title, "utf-8").encode()


def push_captcha_assist(
    *, flavor: str, url: str, title: str, body: str, timeout_s: float = 3.0
) -> bool:
    """按 flavor 拼装并发出接管推送。成功（2xx）→ True；任何失败 → False（不抛）。"""
    flavor = (flavor or "raw").strip().lower()
    base = (url or "").strip()
    if not base:
        log.warning("assist_notify.missing_url", flavor=flavor)
        return False
    try:
        if flavor == "bark":
            # url 是含 key 的 base（如 https://api.day.app/xxxx）；?url= 点了直接打开接管页
            q = urllib.parse.quote
            target = (
                f"{base.rstrip('/')}/{q(title, safe='')}/{q(body, safe='')}"
                f"?url={q(_extract_url(body), safe='')}"
            )
            return _request(urllib.request.Request(target, method="GET"), timeout_s)
        if flavor == "serverchan":
            # Server酱 Turbo（sctapi）兼容：title/desp query 参数
            qs = urllib.parse.urlencode({"title": title, "desp": body})
            return _request(urllib.request.Request(f"{base}?{qs}", method="GET"), timeout_s)
        if flavor == "wecom":
            return _request(
                _json_post(base, {"msgtype": "text",
                                  "text": {"content": f"{title}\n{body}"}}),
                timeout_s,
            )
        if flavor == "ntfy":
            req = urllib.request.Request(
                base,
                data=body.encode("utf-8"),
                headers={"Title": _ntfy_title(title)},
                method="POST",
            )
            return _request(req, timeout_s)
        if flavor == "raw":
            return _request(
                _json_post(base, {"event": "captcha_assist", "title": title,
                                  "body": body, "url": _extract_url(body)}),
                timeout_s,
            )
        log.warning("assist_notify.unknown_flavor", flavor=flavor)
        return False
    except Exception as exc:  # noqa: BLE001 — 推送失败绝不阻断 workflow
        log.warning("assist_notify.push_failed", flavor=flavor, error=str(exc))
        return False
