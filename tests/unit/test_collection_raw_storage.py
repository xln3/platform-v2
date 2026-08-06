"""采集产物原始存储原则（2026-08-06 用户拍板）：公开平台输出原文入库、零 DLP。

DLP 的边界在会话侧秘密（截图/账号页/凭据）与 intake 用户输入；
豆包公开回答、公开引用、平台检索词是测量原料——原文存储，不脱敏不拒绝。
"""

from workflows.activities.collection import _normalize_citations, _normalize_search_queries


def test_answer_like_content_with_phone_passes_raw() -> None:
    findings_free_rows = _normalize_citations(
        [
            {
                "url": "https://example.com/a",
                "title": "安全厂商排名 电话13800138000",
                "cited_text": "咨询热线 13912345678 提供方案",
            }
        ]
    )
    # 公开内容原文保留——手机号样式不被篡改（它是原料，不是平台秘密）
    assert findings_free_rows[0]["title"] == "安全厂商排名 电话13800138000"
    assert findings_free_rows[0]["cited_text"] == "咨询热线 13912345678 提供方案"


def test_public_weixin_url_and_phone_in_queries_pass_raw() -> None:
    rows = _normalize_search_queries(
        [
            {"query": "详见 weixin.qq.com/s/AbCdEfGh1234 的分析", "ordinal": 1},
            {"query": "资产测绘厂商 13800138000 对比", "ordinal": 2},
            {"query": "password= hunter2", "ordinal": 3},
        ]
    )
    assert rows[0]["query"] == "详见 weixin.qq.com/s/AbCdEfGh1234 的分析"
    assert rows[1]["query"] == "资产测绘厂商 13800138000 对比"
    assert rows[2]["query"] == "password= hunter2"


def test_structural_validation_still_enforced() -> None:
    import pytest

    with pytest.raises(ValueError):
        _normalize_citations([{"url": "not-a-url", "title": "x"}])
    with pytest.raises(ValueError):
        _normalize_search_queries([{"query": "q", "ordinal": 0}])
