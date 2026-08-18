from domain.collection.answer_content import project_answer_content
from workflows.activities.collection import _normalize_citations


def test_answer_projection_separates_legacy_reference_appendix_and_maps_zero_based_markers() -> (
    None
):
    raw = (
        "# 结论\r\n\r\n"
        "- 权限边界需要核验 [citation:0]\r\n"
        "- 未映射引用不生成链接 [citation:4]\r\n\r\n"
        "参考来源：\r\n"
        "1. 权威页面 — Example\r\n"
        "   https://example.com/source"
    )
    citations = _normalize_citations(
        [{"url": "https://example.com/source", "title": "权威页面"}],
        answer_text=raw,
    )

    projected = project_answer_content(raw, citations)

    assert projected.response_raw == raw
    assert "参考来源" not in projected.response_markdown_normalized
    assert "https://example.com/source" not in projected.response_markdown_normalized
    assert "[1](#citation-1)" in projected.response_markdown_normalized
    assert "[引用 4]" in projected.response_markdown_normalized
    assert '<a href="#citation-1">1</a>' in projected.response_html_sanitized
    assert "# 结论" not in projected.response_plain_text
    assert projected.response_hash


def test_answer_projection_escapes_raw_html_and_dangerous_links() -> None:
    projected = project_answer_content(
        "<script>alert(1)</script>\n\n"
        "[危险](javascript:alert(1))\n\n"
        "[安全](https://example.com/**path**)"
    )

    assert "<script>" not in projected.response_html_sanitized
    assert "&lt;script&gt;" in projected.response_html_sanitized
    assert "javascript:" not in projected.response_html_sanitized
    assert 'href="https://example.com/**path**"' in projected.response_html_sanitized
    assert "<strong>path</strong>" not in projected.response_html_sanitized


def test_answer_projection_keeps_a_legitimate_empty_reference_heading() -> None:
    projected = project_answer_content("## 结论\n\n参考来源：")

    assert projected.response_markdown_normalized.endswith("参考来源：")


def test_citation_normalization_preserves_repeated_urls_and_one_based_gaps() -> None:
    citations = _normalize_citations(
        [
            {"url": "https://example.com/source", "ordinal": 1, "ordinal_base": 1},
            {"url": "https://example.com/source", "ordinal": 3, "ordinal_base": 1},
        ],
        answer_text="第一处 [citation:1]，第三处 [citation:3]。",
    )

    assert [item["ordinal"] for item in citations] == [1, 3]
    assert [item["platform_ordinal"] for item in citations] == [1, 3]
    assert len(citations) == 2


def test_citation_normalization_infers_zero_base_from_structured_ordinal() -> None:
    citations = _normalize_citations(
        [
            {"url": "https://example.com/zero", "ordinal": 0},
            {"url": "https://example.com/one", "ordinal": 1},
        ]
    )

    assert [item["ordinal"] for item in citations] == [1, 2]
    assert [item["platform_ordinal"] for item in citations] == [0, 1]
    assert {item["ordinal_base"] for item in citations} == {0}


def test_repeated_answer_markers_map_to_the_same_real_relation() -> None:
    citations = _normalize_citations(
        [{"url": "https://example.com/source"}],
        answer_text="前文 [citation:0]，后文再次引用 [citation:0]。",
    )
    projected = project_answer_content("前文 [citation:0]，后文再次引用 [citation:0]。", citations)

    assert projected.response_markdown_normalized.count("(#citation-1)") == 2


def test_citation_normalization_rejects_duplicate_platform_ordinals() -> None:
    try:
        _normalize_citations(
            [
                {"url": "https://example.com/one", "ordinal": 1},
                {"url": "https://example.com/two", "ordinal": 1},
            ]
        )
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate citation ordinals must fail closed")
