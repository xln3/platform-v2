import pytest

from domain.reporting.policy import assert_customer_report_safe


def test_customer_report_allows_business_method_summary_only() -> None:
    assert_customer_report_safe(
        [
            {
                "title": "采集方法",
                "body": "业务渠道：Web；采集方法：经授权会话采集。",
            }
        ]
    )


@pytest.mark.parametrize(
    "section",
    [
        {"platform_account_pub_id": "acct_opaque"},
        {"profile_path": "/tmp/profile"},
        {"body": "Authorization: Bearer secret"},
        {"body": "OTP: 123456"},
    ],
)
def test_customer_report_rejects_account_profile_verification_and_secrets(
    section: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        assert_customer_report_safe([section])
