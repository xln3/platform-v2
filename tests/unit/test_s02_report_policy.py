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


def test_customer_report_treats_valid_sha256_trace_as_opaque() -> None:
    # This real random trace contains an 11-digit substring beginning with 1;
    # prose DLP used to misclassify it as a phone number and make the integration
    # suite flaky.
    assert_customer_report_safe(
        [{"trace_token": ("b64cfe55ad14443031510b29df37bc4a481a102162e7d9c0ab2c326223f88169")}]
    )


def test_customer_report_does_not_exempt_non_hash_trace_values() -> None:
    with pytest.raises(ValueError, match="secret-bearing"):
        assert_customer_report_safe([{"trace_token": "contact 13800138000"}])


def test_customer_report_treats_source_count_keys_as_customer_data() -> None:
    assert_customer_report_safe(
        [{"sources": {"sitename_counts": {"riskprofiler.io": 1, "profile.example": 2}}}]
    )


def test_customer_report_still_checks_schema_below_dynamic_source_keys() -> None:
    with pytest.raises(ValueError, match="operational provenance"):
        assert_customer_report_safe(
            [
                {
                    "sources": {
                        "sitename_counts": {
                            "riskprofiler.io": {"proxy_url": "https://example.test"}
                        }
                    }
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
