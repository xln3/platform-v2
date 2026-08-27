from domain.brandrank.entities import (
    load_entity_master,
    normalize_answer_entities,
    summarize_entity_resolution,
)
from domain.brandrank.rules import load_domain


def _normalized(values: list[str], *, comparison_scopes: tuple[str, ...] = ()):
    return normalize_answer_entities(
        values,
        rules=load_domain("cybersecurity"),
        master=load_entity_master("cybersecurity"),
        target_brand="盛邦安全",
        comparison_scopes=comparison_scopes,
    )


def test_aliases_merge_once_with_first_answer_rank() -> None:
    rows = _normalized(
        ["奇安信", "绿盟", "绿盟科技", "Palo Alto", "Palo Alto Networks", "Microsoft", "微软"]
    )

    assert [row["canonical_name"] for row in rows] == [
        "奇安信",
        "绿盟科技",
        "Palo Alto Networks",
        "微软",
    ]
    assert rows[1]["raw_aliases"] == ["绿盟", "绿盟科技"]
    assert [row["answer_rank"] for row in rows] == [1, 2, 3, 4]


def test_tools_and_institutions_are_not_competitors() -> None:
    rows = _normalized(["Nmap", "OWASP Amass", "河北中鑫会计师事务所有限公司", "绿盟"])
    by_name = {row["canonical_name"]: row for row in rows}

    assert by_name["Nmap"]["entity_type"] == "tool"
    assert by_name["Nmap"]["competitor_eligible"] is False
    assert by_name["Amass"]["competitor_eligible"] is False
    assert by_name["河北中鑫会计师事务所有限公司"]["entity_type"] == "unknown"
    assert by_name["河北中鑫会计师事务所有限公司"]["competitor_eligible"] is False
    assert by_name["绿盟科技"]["competitor_eligible"] is True


def test_unclassified_entity_is_retained_but_fail_closed() -> None:
    row = _normalized(["尚未治理的新实体"])[0]

    assert row["canonical_name"] == "尚未治理的新实体"
    assert row["entity_type"] == "unknown"
    assert row["competitor_eligible"] is False
    assert row["classification_source"] == "unclassified"


def test_governed_resolution_does_not_fuzzy_merge_unreviewed_similar_names() -> None:
    rows = _normalized(["腾讯云安全实验室", "绿盟科技生态伙伴", "上海市数字证书认证中心有限公司"])

    assert [row["entity_type"] for row in rows] == ["unknown", "unknown", "company"]
    assert [row["canonical_name"] for row in rows] == [
        "腾讯云安全实验室",
        "绿盟科技生态伙伴",
        "上海CA",
    ]


def test_brand_family_aliases_merge_once_across_company_cloud_and_abbreviation() -> None:
    rows = _normalized(
        [
            "腾讯云",
            "腾讯",
            "腾讯安全",
            "华为云",
            "华为",
            "绿盟",
            "绿盟科技",
            "北京数字认证股份有限公司",
            "数字认证",
            "BJCA",
            "新大陆（福建）公共服务有限公司",
            "新大陆",
        ]
    )

    assert [row["canonical_name"] for row in rows] == [
        "腾讯",
        "华为",
        "绿盟科技",
        "数字认证",
        "新大陆",
    ]
    assert rows[0]["raw_aliases"] == ["腾讯云", "腾讯", "腾讯安全"]
    assert rows[1]["raw_aliases"] == ["华为云", "华为"]
    assert rows[2]["raw_aliases"] == ["绿盟", "绿盟科技"]
    assert rows[3]["raw_aliases"] == ["北京数字认证股份有限公司", "数字认证", "BJCA"]
    assert rows[4]["raw_aliases"] == ["新大陆（福建）公共服务有限公司", "新大陆"]
    assert [row["answer_rank"] for row in rows] == [1, 2, 3, 4, 5]


def test_disputed_companies_keep_scope_and_legal_entity_caveats() -> None:
    by_name = {row["canonical_name"]: row for row in _normalized(["数字认证", "新大陆"])}

    assert by_name["数字认证"]["industry_fit"] == "core_cybersecurity"
    assert "cybersecurity" in by_name["数字认证"]["competitor_scopes"]
    assert by_name["新大陆"]["industry_fit"] == "scenario_specific_adjacent"
    assert by_name["新大陆"]["competitor_eligible"] is False
    assert by_name["新大陆"]["eligibility_mode"] == "scope_required"
    assert "ctid" in by_name["新大陆"]["competitor_scopes"]
    assert "不等于认定各主体为同一法人" in by_name["新大陆"]["eligibility_note"]
    scoped = _normalized(["新大陆"], comparison_scopes=("ctid",))[0]
    assert scoped["competitor_eligible"] is True


def test_resolution_summary_exposes_aliases_and_unknown_review_queue() -> None:
    master = load_entity_master("cybersecurity")
    summary = summarize_entity_resolution(
        [
            ["腾讯云", "腾讯", "Nmap", "尚未治理的新实体"],
            ["绿盟", "数字认证"],
        ],
        rules=load_domain("cybersecurity"),
        master=master,
        target_brand="盛邦安全",
    )

    assert summary["mode"] == "siliconindex_entity_governance_v1"
    assert summary["master"]["revision"] == master.revision
    assert summary["master"]["source_system"] == "siliconindex"
    assert summary["master"]["source_release_id"] == master.source_release_id
    assert summary["master"]["source_mode"] == "bundled_siliconindex_projection"
    assert summary["counts"]["raw_mentions"] == 6
    assert summary["counts"]["canonical_answer_mentions"] == 5
    assert summary["counts"]["alias_collapses_within_answers"] == 1
    assert summary["counts"]["unclassified_distinct_names"] == 1
    assert summary["excluded_by_type"] == {"tool": 1, "unknown": 1}
    assert summary["counts"]["pending_review_distinct_names"] == 2
    assert summary["pending_review"] == [
        {
            "observed_name": "Nmap",
            "answer_mentions": 1,
            "raw_aliases": ["Nmap"],
            "status": "pending_governance_review",
        },
        {
            "observed_name": "尚未治理的新实体",
            "answer_mentions": 1,
            "raw_aliases": ["尚未治理的新实体"],
            "status": "pending_semantic_review",
        },
    ]
    mappings = {row["canonical_name"]: row for row in summary["applied_mappings"]}
    assert mappings["腾讯"]["raw_aliases"] == ["腾讯", "腾讯云"]
    assert mappings["腾讯"]["relationships"] == ["business_unit_of", "self"]


def test_high_frequency_candidates_are_governed_with_non_binary_industry_fit() -> None:
    rows = _normalized(
        [
            "神思电子技术股份有限公司",
            "吉大正元",
            "FOFA",
            "天威诚信",
            "中盾安信",
            "卫士通",
            "格尔",
            "Authing身份云",
            "ZKTeco",
            "River Security",
        ]
    )
    by_name = {row["canonical_name"]: row for row in rows}

    assert set(by_name) == {
        "神思电子",
        "吉大正元",
        "华顺信安",
        "天威诚信",
        "中盾安信",
        "电科网安",
        "格尔软件",
        "Authing",
        "熵基科技",
        "瑞数信息",
    }
    assert by_name["吉大正元"]["industry_fit"] == "core_cybersecurity"
    assert by_name["神思电子"]["industry_fit"] == "scenario_specific_adjacent"
    assert by_name["Authing"]["entity_type"] == "product"
    assert by_name["电科网安"]["relationship_to_canonical"] == "historical_name"


def test_second_review_batch_keeps_core_specialist_integrator_and_adjacent_roles() -> None:
    by_name = {
        row["canonical_name"]: row
        for row in _normalized(
            [
                "宁盾",
                "信安世纪",
                "铭冠网安",
                "上海CA",
                "南威软件",
                "三未信安",
                "数安时代",
                "海泰方圆",
                "魔方安全",
                "派拉软件",
            ]
        )
    }

    assert by_name["信安世纪"]["industry_fit"] == "core_cybersecurity"
    assert by_name["宁盾"]["industry_fit"] == "identity_security_specialist"
    assert by_name["铭冠网安"]["industry_fit"] == "cybersecurity_integrator"
    assert by_name["南威软件"]["industry_fit"] == "scenario_specific_adjacent"
    assert by_name["上海CA"]["entity_type"] == "company"
