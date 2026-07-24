import re

from tools.migration.migrate_legacy_core import ROLE_MAP, deterministic_pub_id


def test_every_legacy_role_maps_to_a_supported_v2_role() -> None:
    supported = {"customer", "operator", "analyst", "reviewer", "admin", "worker"}
    assert set(ROLE_MAP) == {"owner", "admin", "member", "viewer"}
    assert set(ROLE_MAP.values()) <= supported
    assert ROLE_MAP["owner"] == "admin"
    assert ROLE_MAP["viewer"] == "customer"


def test_deterministic_migration_ids_use_crockford_ulid_alphabet() -> None:
    pattern = re.compile(r"^prj_[0-9A-HJKMNP-TV-Z]{26}$")
    for source_pk in range(1_000):
        assert pattern.fullmatch(deterministic_pub_id("prj", "a" * 64, "project", str(source_pk)))
