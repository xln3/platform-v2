from __future__ import annotations

from tools.generate_formal_review_reports import _PUB_ID_RE


def test_cli_accepts_platform_base32_public_ids() -> None:
    assert _PUB_ID_RE.fullmatch("tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y")
    assert _PUB_ID_RE.fullmatch("prj_68ER9J6QBX054EAX52G7BEF7PH")
    assert not _PUB_ID_RE.fullmatch("../tenant")
    assert not _PUB_ID_RE.fullmatch("TNT_invalid_prefix")
