"""一次性自动化开户驱动（V2 京沪矩阵）：复用旧链已校准的 doubao SMS-OTP 登录。

用法：server/.venv/bin/python tools/auto_login_doubao.py <profile_dir> <phone> <relay_port> [city]
验证码经 V2 OTP 推送（SmsForwarder → /api/v2/otp/push → runtime/otp_inbox）自动取码。
"""

import sys
from pathlib import Path

sys.path.insert(0, "/home/xln/geo-system/server")

from proxyllm.doubao_client import login_into_profile
from proxyllm.webhook_otp_relay import WebhookOtpRelay


class _RelayProxy:
    """最小 proxy shim：login_into_profile 只读 .playwright_proxy / .candidate。"""

    def __init__(self, relay_port: str) -> None:
        self.playwright_proxy = {"server": f"http://127.0.0.1:{relay_port}"}
        self.candidate = None


def main() -> int:
    profile_dir, phone, relay_port = sys.argv[1], sys.argv[2], sys.argv[3]
    city = sys.argv[4] if len(sys.argv) > 4 else ""
    notifier = WebhookOtpRelay(
        target_phone=phone,
        inbox_dir=Path("/home/xln/geo-system/platform-v2/runtime/otp_inbox"),
        match_any_phone=True,
    )
    result = login_into_profile(
        _RelayProxy(relay_port),
        user_data_dir=Path(profile_dir),
        notifier=notifier,
        phone=phone,
        city=city,
        headless=False,
        timeout_min=5,
    )
    print("LOGIN_RESULT:", result.get("status"))
    return 0 if result.get("status") == "logged_in" else 1


if __name__ == "__main__":
    raise SystemExit(main())
