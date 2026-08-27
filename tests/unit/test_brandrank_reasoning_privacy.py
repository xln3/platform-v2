from __future__ import annotations

from geo_platform.brandrank.service import _bounded_model_context


def test_model_context_is_mention_bounded_and_redacts_contact_data() -> None:
    prefix = "不相关客户原文" * 100
    suffix = "另一段不相关原文" * 100
    value = (
        f"{prefix} 云盾X用于云安全。联系 alice@example.com，"
        f"电话 138 0013 8000，详情 https://example.test/private?q=1 {suffix}"
    )
    result = _bounded_model_context(value, "云盾X")

    assert "云盾X用于云安全" in result
    assert "alice@example.com" not in result
    assert "138 0013 8000" not in result
    assert "https://" not in result
    assert "[email]" in result and "[number]" in result and "[url]" in result
    assert len(result) <= 2 * 160 + len("云盾X")
    assert prefix not in result and suffix not in result


def test_model_context_does_not_send_an_answer_without_the_mention() -> None:
    assert _bounded_model_context("这是别的回答", "云盾X") == ""
