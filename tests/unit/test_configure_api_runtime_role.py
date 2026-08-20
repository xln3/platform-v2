from tools.configure_api_runtime_role import SCHEMAS


def test_runtime_roles_are_granted_access_to_the_sop_schema() -> None:
    assert "sop" in SCHEMAS


def test_runtime_roles_are_granted_access_to_the_posting_schema() -> None:
    assert "posting" in SCHEMAS
