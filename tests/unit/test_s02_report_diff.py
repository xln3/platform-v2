from domain.reporting.diff import compare_report_versions


def test_report_version_diff_is_auditable() -> None:
    result = compare_report_versions(
        before_version=1,
        after_version=2,
        before_components=[{"title": "摘要", "body": "旧结论"}],
        after_components=[
            {"title": "摘要", "body": "人工复核后的结论"},
            {"title": "行动", "body": "负责人：客户团队"},
        ],
    )
    assert result.changed_component_count == 2
    assert "人工复核后的结论" in result.unified_diff
