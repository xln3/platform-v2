from __future__ import annotations

from dataclasses import replace

from domain.source_analysis.content_contribution import (
    exact_answer_chunks,
    explicit_citation_chunk,
    validate_chunk,
)
from workflows.activities.content_contribution import _citation_text


def test_exact_answer_chunk_carries_replayable_source_and_answer_spans() -> None:
    quote = "该产品覆盖一百二十种疾病，并包含轻症豁免保费责任。"
    source = f"页面标题\n{quote}\n其他正文"
    answer = f"综合公开材料：\n- {quote}\n建议核对正式条款。"

    chunks = exact_answer_chunks(source_text=source, answer_text=answer)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert source[chunk.source_text_start : chunk.source_text_end] == chunk.source_quote
    assert answer[chunk.answer_text_start : chunk.answer_text_end] == chunk.answer_quote
    assert validate_chunk(chunk, source_text=source, answer_text=answer)


def test_tampered_exact_span_invalidates_the_entire_chunk() -> None:
    quote = "这是一个足够长并且可以逐字验证的来源句子。"
    source = f"前文。{quote}后文。"
    answer = quote
    chunk = exact_answer_chunks(source_text=source, answer_text=answer)[0]

    assert not validate_chunk(
        replace(chunk, source_text_start=chunk.source_text_start + 1),
        source_text=source,
        answer_text=answer,
    )
    assert not validate_chunk(chunk, source_text=source.replace("逐字", "精确"), answer_text=answer)


def test_url_reference_without_exact_cited_text_is_not_w() -> None:
    source = "来源正文只包含经过核验的公开事实。"

    assert explicit_citation_chunk(source_text=source, cited_text=None) is None
    assert explicit_citation_chunk(source_text=source, cited_text="不存在的引文") is None


def test_exact_platform_citation_can_create_content_level_w_candidate() -> None:
    quote = "经过核验的公开事实"
    source = f"来源正文只包含{quote}。"

    chunk = explicit_citation_chunk(source_text=source, cited_text=quote)

    assert chunk is not None
    assert chunk.basis == "explicit_citation"
    assert source[chunk.source_text_start : chunk.source_text_end] == quote


def test_w_citation_evidence_uses_citation_quote_not_search_summary() -> None:
    row = {
        "final_reference_state": "referenced",
        "final_reference_ordinal": 2,
        "canonical_url": "https://example.com/article",
        "citations_json": """[
          {"url":"https://other.example/a","ordinal":1,"cited_text":"其他引文"},
          {"url":"https://example.com/article#anchor","ordinal":2,
           "cited_text":"平台逐字引用"}
        ]""",
    }

    assert _citation_text(row) == "平台逐字引用"


def test_repeated_url_citations_without_matching_ordinal_are_ambiguous() -> None:
    row = {
        "final_reference_state": "referenced",
        "final_reference_ordinal": 9,
        "canonical_url": "https://example.com/article",
        "citations_json": """[
          {"url":"https://example.com/article","ordinal":1,"cited_text":"第一处"},
          {"url":"https://example.com/article","ordinal":2,"cited_text":"第二处"}
        ]""",
    }

    assert _citation_text(row) is None
