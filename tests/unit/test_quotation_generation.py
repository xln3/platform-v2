"""报价单：XLSX 解析、LLM 内容契约、DOCX 格式与端到端编排。"""

from __future__ import annotations

from collections import Counter
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import geo_platform.quotations.router as quotation_router
import pytest
from docx import Document
from fastapi.testclient import TestClient
from geo_platform.config import Settings
from geo_platform.identity.policy import Principal, Role, get_principal
from geo_platform.main import app
from geo_platform.quotations.generator import (
    QuotationGenerationFailed,
    plan_from_payload,
    selection_quotas,
)
from geo_platform.quotations.models import TargetQuery
from geo_platform.quotations.renderer import render_quotation_docx
from geo_platform.quotations.service import generate_quotation
from geo_platform.quotations.xlsx import TargetWorkbookInvalid, parse_target_queries


def _xlsx(rows: list[dict[str, str]], *, extra_name: str | None = None) -> bytes:
    row_xml: list[str] = []
    for row_number, cells in enumerate(rows, start=1):
        rendered = "".join(
            f'<c r="{column}{row_number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for column, value in cells.items()
        )
        row_xml.append(f'<row r="{row_number}">{rendered}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>"
    )
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="目标词" sheetId="1" r:id="rId1"/></sheets>
    </workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
        Target="worksheets/sheet1.xml"/>
    </Relationships>"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        if extra_name:
            archive.writestr(extra_name, "hostile")
    return output.getvalue()


def _queries() -> list[TargetQuery]:
    result: list[TargetQuery] = []
    index = 1
    for group in ("网空线", "防护线", "星网线", "检测线", "安服线"):
        for item in range(1, 5):
            result.append(
                TargetQuery(
                    query_id=f"Q{index:03d}",
                    group=group,
                    text=f"{group}目标问题{item}",
                    sheet="目标词",
                    row=index + 2,
                )
            )
            index += 1
    return result


def _llm_payload(queries: list[TargetQuery]) -> dict[str, object]:
    quotas = selection_quotas(queries)
    selected: list[dict[str, str]] = []
    for group, quota in quotas.items():
        for query in [row for row in queries if row.group == group][:quota]:
            selected.append(
                {
                    "source_id": query.query_id,
                    "variant_a": f"围绕{query.text}，国内主要产品与服务商有哪些？",
                    "variant_b": f"企业评估{query.text}相关方案时应关注哪些选型维度？",
                    "variant_c": f"想解决{query.text}，找哪家公司比较靠谱？",
                }
            )
    opportunities = [
        {
            "keyword": f"相邻业务机会词{index}",
            "optimized_query": f"相邻业务机会词{index}有哪些成熟产品和服务商？",
            "variant_a": f"国内提供相邻业务机会词{index}解决方案的主要厂商有哪些？",
            "variant_b": f"采购相邻业务机会词{index}方案时应比较哪些技术维度？",
            "variant_c": f"相邻业务机会词{index}这块找谁做比较靠谱？",
            "rewrite_rationale": "由概念了解转向产品选型、厂商推荐与方案比较意图",
        }
        for index in range(1, 17)
    ]
    return {
        "profile": {
            "category_label": "网络安全产品与服务",
            "sec_profile": "trust",
            "category_analysis": (
                "该类产品专业门槛较高，采购方在购买前难以仅凭参数判断实际效果，"
                "通常需要结合专业评测、适用场景、资质材料与可信第三方信息进行决策。"
            ),
            "intent_diagnosis": (
                "原始目标词兼有概念了解、问题解决和厂商选型意图。改写时保留核心业务语义，"
                "并明确产品、服务商、比较维度或应用场景，形成可验证的推荐型查询。"
            ),
        },
        "selected_queries": selected,
        "opportunities": opportunities,
        "sources": [{"title": "品牌官网", "url": "https://example.com/brand"}],
    }


def _workbook_for_queries(queries: list[TargetQuery]) -> bytes:
    rows: list[dict[str, str]] = [{"B": "产线"}]
    last_group = ""
    sequence = 1
    for query in queries:
        if query.group != last_group:
            rows.append({"A": "序号", "B": f"{query.group}-关键问题"})
            last_group = query.group
            sequence = 1
        rows.append({"A": str(sequence), "B": query.text})
        sequence += 1
    return _xlsx(rows)


def test_xlsx_parser_preserves_group_order_and_deduplicates() -> None:
    payload = _xlsx(
        [
            {"B": "产线"},
            {"A": "序号", "B": "网空线-关键问题"},
            {"A": "1", "B": "企业未知资产怎么排查"},
            {"A": "2", "B": "资产测绘平台推荐"},
            {"A": "序号", "B": "防护线-关键问题"},
            {"A": "1", "B": "老旧系统打不了补丁怎么办"},
            {"A": "2", "B": "资产测绘平台推荐"},
        ]
    )
    rows = parse_target_queries(payload)
    assert [(row.query_id, row.group, row.text) for row in rows] == [
        ("Q001", "网空线", "企业未知资产怎么排查"),
        ("Q002", "网空线", "资产测绘平台推荐"),
        ("Q003", "防护线", "老旧系统打不了补丁怎么办"),
    ]


def test_xlsx_parser_rejects_non_xlsx_and_archive_traversal() -> None:
    with pytest.raises(TargetWorkbookInvalid, match="xlsx_signature_invalid"):
        parse_target_queries(b"not-an-xlsx")
    with pytest.raises(TargetWorkbookInvalid, match="xlsx_archive_path_invalid"):
        parse_target_queries(_xlsx([{"A": "目标词"}, {"A": "测试问题"}], extra_name="../x"))


def test_selection_quota_matches_reference_balance() -> None:
    assert selection_quotas(_queries()) == {
        "网空线": 4,
        "防护线": 4,
        "星网线": 4,
        "检测线": 3,
        "安服线": 3,
    }


def test_llm_payload_is_bound_back_to_uploaded_source_and_group_quota() -> None:
    queries = _queries()
    payload = _llm_payload(queries)
    plan = plan_from_payload(payload, queries=queries, model="fixture-model")
    source = {row.query_id: row for row in queries}
    assert len(plan.selected_queries) == 18
    assert Counter(row.group for row in plan.selected_queries) == selection_quotas(queries)
    assert all(row.original == source[row.source_id].text for row in plan.selected_queries)
    assert len(plan.opportunities) == 16
    assert plan.sources[0].url == "https://example.com/brand"


def test_llm_payload_rejects_unverified_effect_numbers() -> None:
    queries = _queries()
    payload = _llm_payload(queries)
    profile = payload["profile"]
    assert isinstance(profile, dict)
    profile["intent_diagnosis"] = "优化后厂商推荐提升37次，因此可以直接确认品牌表现已经明显改善。"
    with pytest.raises(QuotationGenerationFailed, match="llm_unverified_measurement_claim"):
        plan_from_payload(payload, queries=queries, model="fixture-model")


def test_renderer_keeps_reference_layout_and_honest_measurement_wording() -> None:
    queries = _queries()
    plan = plan_from_payload(_llm_payload(queries), queries=queries, model="fixture-model")
    payload = render_quotation_docx(
        brand_name="盛邦安全",
        quote_date=date(2026, 8, 10),
        plan=plan,
    )
    assert payload.startswith(b"PK")
    document = Document(BytesIO(payload))
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.page_width is not None
    assert section.page_height is not None
    assert section.left_margin is not None
    assert section.top_margin is not None
    assert round(section.page_width.mm) == 210
    assert round(section.page_height.mm) == 297
    assert round(section.left_margin.mm) == 15
    assert round(section.top_margin.mm) == 18
    assert len(document.tables) == 2
    assert len(document.tables[0].rows) == 6
    assert len(document.tables[0].columns) == 4
    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "盛邦安全" in body_text
    assert "附录一 Query优化方案说明" in body_text
    assert "附录二 原推广Query与变体构建说明" in body_text
    assert "附录三 新增Query优化与语义变体全表" in body_text
    table_text = "\n".join(
        cell.text for table in document.tables for row in table.rows for cell in row.cells
    )
    assert "待实测" in table_text
    assert "厂商推荐15次" not in body_text
    document_text = f"{body_text}\n{table_text}"
    assert "己方GEO内容" not in document_text
    assert "AI回答所列第三方信源页面" in document_text
    with ZipFile(BytesIO(payload)) as archive:
        header = archive.read("word/header1.xml").decode()
        footer = archive.read("word/footer1.xml").decode()
        document_xml = archive.read("word/document.xml").decode()
    assert "本报价为保密商业资料" in header
    assert " PAGE " in footer
    assert document_xml.count('w:type="page"') >= 3


def test_service_runs_one_pipeline_and_returns_integrity_metadata() -> None:
    queries = _queries()
    llm_payload = _llm_payload(queries)
    calls: list[dict[str, object]] = []

    def runner(
        *args: object, **kwargs: object
    ) -> tuple[dict[str, object], list[dict[str, str]], dict[str, int]]:
        calls.append(kwargs)
        return llm_payload, [], {"input_tokens": 10, "output_tokens": 20}

    result = generate_quotation(
        brand_name="盛邦安全",
        workbook_payload=_workbook_for_queries(queries),
        settings=Settings(research_llm_api_key="unit-test-key"),
        quote_date=date(2026, 8, 10),
        runner=runner,
    )
    assert len(calls) == 1
    assert result.metadata.filename == "报价单-盛邦安全-20260810.docx"
    assert result.metadata.target_query_count == 20
    assert result.metadata.selected_query_count == 18
    assert result.metadata.opportunity_count == 16
    assert len(result.metadata.sha256) == 64
    assert result.payload.startswith(b"PK")


def test_generate_api_returns_named_no_store_docx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = _queries()
    llm_payload = _llm_payload(queries)

    def runner(
        *args: object, **kwargs: object
    ) -> tuple[dict[str, object], list[dict[str, str]], dict[str, int]]:
        return llm_payload, [], {"input_tokens": 10, "output_tokens": 20}

    generated = generate_quotation(
        brand_name="盛邦安全",
        workbook_payload=_workbook_for_queries(queries),
        settings=Settings(research_llm_api_key="unit-test-key"),
        quote_date=date(2026, 8, 10),
        runner=runner,
    )
    monkeypatch.setattr(quotation_router, "generate_quotation", lambda **_: generated)
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="operator@example.test",
        role=Role.OPERATOR,
        tenant_pub_id="ten_unit",
        user_pub_id="usr_unit",
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v2/quotations/generate",
                data={"brand_name": "盛邦安全", "quote_date": "2026-08-10"},
                files={
                    "target_words": (
                        "目标词.xlsx",
                        _workbook_for_queries(queries),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 200
    assert response.content == generated.payload
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-quotation-target-query-count"] == "20"
    assert "UTF-8''" in response.headers["content-disposition"]
