import json
from types import SimpleNamespace

import pytest

from domain.brandrank.entities import load_entity_master
from domain.brandrank.resolution import (
    PROMPT_VERSION,
    ResolutionError,
    build_resolution_prompt,
    parse_resolution_response,
    resolve_candidates_with_llm,
)


@pytest.fixture(scope="module")
def master():
    return load_entity_master("cybersecurity")


@pytest.fixture
def candidates():
    return [
        {
            "observed_name": "腾讯云",
            "answer_mentions": 12,
            "contexts": ["腾讯云提供云安全方案；同一回答还写了腾讯。"],
        },
        {
            "observed_name": "待确认品牌",
            "answer_mentions": 2,
            "contexts": ["回答把待确认品牌列为供应商，但没有完整公司名。"],
        },
    ]


def _valid_document(master):
    tencent = master.alias_index["腾讯云"]
    return {
        "prompt_version": PROMPT_VERSION,
        "decisions": [
            {
                "observed_name": "腾讯云",
                "decision": "merge_existing",
                "matched_entity_id": tencent.entity_id,
                "canonical_name": "腾讯",
                "entity_type": "company",
                "relationship": "business_unit_of",
                "competitor_eligible_for_scope": True,
                "applicable_scopes": ["cloud_security"],
                "confidence": 0.98,
                "context_evidence": ["腾讯云提供云安全方案"],
                "reasoning": "腾讯云是已审核主数据中的腾讯品牌家族业务线。",
                "external_verification_needed": False,
            },
            {
                "observed_name": "待确认品牌",
                "decision": "ambiguous",
                "matched_entity_id": None,
                "canonical_name": None,
                "entity_type": "unknown",
                "relationship": "uncertain",
                "competitor_eligible_for_scope": None,
                "applicable_scopes": [],
                "confidence": 0.4,
                "context_evidence": ["列为供应商"],
                "reasoning": "缺少可唯一定位真实主体的信息。",
                "external_verification_needed": True,
            },
        ],
    }


def test_prompt_separates_identity_scope_and_legal_entity(master, candidates) -> None:
    prompt = build_resolution_prompt(
        candidates,
        master=master,
        comparison_scope="网证/可信数字身份接入",
    )

    assert "是不是同一品牌实体" in prompt
    assert "竞品资格" in prompt
    assert "品牌归并不等于同一法人" in prompt
    assert "禁止仅凭字符串包含" in prompt
    assert "网证/可信数字身份接入" in prompt
    assert '"observed_name":"腾讯云"' in prompt
    assert '"canonical_name":"腾讯"' in prompt


def test_parser_binds_proposal_to_candidate_order_and_master_revision(master, candidates) -> None:
    parsed = parse_resolution_response(
        json.dumps(_valid_document(master), ensure_ascii=False),
        candidates=candidates,
        master=master,
    )

    assert parsed["requires_human_review"] is True
    assert parsed["master_revision"] == master.revision
    assert parsed["decisions"][0]["canonical_name"] == "腾讯"
    assert parsed["decisions"][0]["requires_human_review"] is True


def test_parser_rejects_hallucinated_master_identity_and_reordered_candidates(
    master, candidates
) -> None:
    bad_id = _valid_document(master)
    bad_id["decisions"][0]["matched_entity_id"] = "invented:entity"
    with pytest.raises(ResolutionError, match="known_entity_id"):
        parse_resolution_response(
            json.dumps(bad_id, ensure_ascii=False), candidates=candidates, master=master
        )

    reordered = _valid_document(master)
    reordered["decisions"].reverse()
    with pytest.raises(ResolutionError, match="candidate_set_or_order_mismatch"):
        parse_resolution_response(
            json.dumps(reordered, ensure_ascii=False), candidates=candidates, master=master
        )


def test_parser_rejects_fabricated_context_evidence_and_unverified_new_entity(
    master, candidates
) -> None:
    fabricated = _valid_document(master)
    fabricated["decisions"][0]["context_evidence"] = ["输入中不存在的证据"]
    with pytest.raises(ResolutionError, match="context_evidence_not_in_input"):
        parse_resolution_response(
            json.dumps(fabricated, ensure_ascii=False), candidates=candidates, master=master
        )

    unverified = _valid_document(master)
    unverified["decisions"][1].update(
        {
            "decision": "create_entity",
            "canonical_name": "待确认品牌",
            "entity_type": "company",
            "relationship": "independent",
            "competitor_eligible_for_scope": True,
            "external_verification_needed": False,
        }
    )
    with pytest.raises(ResolutionError, match="new_entity_requires_external_verification"):
        parse_resolution_response(
            json.dumps(unverified, ensure_ascii=False), candidates=candidates, master=master
        )


def test_llm_adapter_returns_proposal_without_mutating_master(master, candidates) -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(_valid_document(master), ensure_ascii=False)
                    )
                )
            ]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = resolve_candidates_with_llm(
        client,
        candidates,
        master=master,
        comparison_scope="网证",
        model="best-model",
    )

    assert result["schema_version"] == "brand-entity-resolution-proposal-v1"
    assert result["requires_human_review"] is True
    assert calls[0]["model"] == "best-model"
    assert calls[0]["temperature"] == 0
    assert calls[0]["response_format"] == {"type": "json_object"}
