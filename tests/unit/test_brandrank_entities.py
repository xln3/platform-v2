from domain.brandrank.entities import load_entity_master, normalize_answer_entities
from domain.brandrank.rules import load_domain


def _normalized(values: list[str]):
    return normalize_answer_entities(
        values,
        rules=load_domain("cybersecurity"),
        master=load_entity_master("cybersecurity"),
        target_brand="盛邦安全",
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
    assert by_name["河北中鑫会计师事务所有限公司"]["entity_type"] == "institution"
    assert by_name["绿盟科技"]["competitor_eligible"] is True


def test_unclassified_entity_is_retained_but_fail_closed() -> None:
    row = _normalized(["尚未治理的新实体"])[0]

    assert row["canonical_name"] == "尚未治理的新实体"
    assert row["entity_type"] == "unknown"
    assert row["competitor_eligible"] is False
    assert row["classification_source"] == "unclassified"
