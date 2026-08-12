from __future__ import annotations

from tools import certify_production_release


def test_legacy_certifier_requires_all_48_production_browser_checks() -> None:
    assert certify_production_release.PRODUCTION_BROWSER_TOTAL == 48
    assert certify_production_release.acceptance_summary_passed(
        {"summary": {"total": 48, "passed": 48}},
        expected_total=certify_production_release.PRODUCTION_BROWSER_TOTAL,
    )
    assert not certify_production_release.acceptance_summary_passed(
        {"summary": {"total": 45, "passed": 45}},
        expected_total=certify_production_release.PRODUCTION_BROWSER_TOTAL,
    )
    assert not certify_production_release.acceptance_summary_passed(
        {"summary": {"total": 48, "passed": 47}},
        expected_total=certify_production_release.PRODUCTION_BROWSER_TOTAL,
    )
