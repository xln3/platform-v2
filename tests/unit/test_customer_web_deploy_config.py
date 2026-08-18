from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_customer_web_redirects_missing_trailing_slash_to_canonical_route() -> None:
    locations = (ROOT / "deploy/production/nginx-v2-locations.conf").read_text(encoding="utf-8")

    assert "location = /platform/customer {" in locations
    assert "return 308 /platform/customer/;" in locations
