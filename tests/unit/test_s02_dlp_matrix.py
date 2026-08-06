import pytest

from domain.evidence.dlp import redact_bytes


@pytest.mark.parametrize(
    ("mime_type", "payload"),
    [
        ("text/html", b"<div>Authorization: Bearer secret-token</div>"),
        (
            "application/har+json",
            b'{"request":{"headers":[{"name":"Cookie","value":"sid=secret"}]}}',
        ),
        (
            "application/vnd.geo.ocr+json",
            '{"text":"验证码：123456，手机号 13800138000"}'.encode(),
        ),
        ("application/json", b'{"exception":"access_token=secret"}'),
        ("text/csv", b"field,value\nproxy_password,secret"),
        ("text/plain", b"OTP: 654321"),
    ],
)
def test_dlp_matrix_removes_secrets_before_searchable_admission(
    mime_type: str, payload: bytes
) -> None:
    result = redact_bytes(payload, mime_type=mime_type)
    lowered = result.redacted.lower()
    for secret in (b"secret-token", b"sid=secret", b"123456", b"13800138000", b"654321"):
        assert secret not in lowered


@pytest.mark.parametrize(
    "marker",
    [b"otpauth://totp/secret", b"Authorization: Bearer secret", b"Cookie: sid=secret"],
)
def test_binary_screenshot_pdf_qr_secret_markers_fail_closed(marker: bytes) -> None:
    with pytest.raises(ValueError, match="binary object"):
        redact_bytes(b"\x89PNG\r\n" + marker, mime_type="image/png")


@pytest.mark.parametrize(
    "payload",
    [
        b"otpauth://totp/geo-platform?secret=ABC123",
        b"weixin://wxpay/bizpayurl?pr=abc123XYZ",
        b"alipay://qr/anything12345",
        b"ALIPAYS://platformapi/startapp?saId=123456",
    ],
)
def test_qr_payload_schemes_are_redacted(payload: bytes) -> None:
    result = redact_bytes(payload, mime_type="text/plain")
    assert "qr_payload" in result.findings
    assert b"[REDACTED:qr_payload]" in result.redacted


@pytest.mark.parametrize(
    "payload",
    [
        # 公开微信公众号文章链接、含 qrcode 字样的普通 URL——不是 QR 秘密载荷。
        "引用来源 https://weixin.qq.com/s/AbCdEfGh1234 的公众号文章".encode(),
        b"see https://example.com/qrcode/generate?content=hello for details",
        "微信支付介绍页 https://pay.weixin.qq.com/doc/index.html".encode(),
    ],
)
def test_public_weixin_and_qrcode_urls_are_not_secret(payload: bytes) -> None:
    result = redact_bytes(payload, mime_type="text/plain")
    assert "qr_payload" not in result.findings
    assert result.redacted == payload
