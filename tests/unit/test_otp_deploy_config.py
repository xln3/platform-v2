from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_apk_path_is_pinned_outside_release_snapshots() -> None:
    """Ignored APK binaries must remain reachable from immutable API releases."""
    drop_in = (
        ROOT / "deploy/production/geo-platform-v2-otp-apk.conf"
    ).read_text(encoding="utf-8")

    assert (
        "GEO_OTP_APK_PATH=/home/xln/geo-system/platform-v2/runtime/smsforwarder.apk"
        in drop_in
    )
    assert ".deploy-backups" not in drop_in
