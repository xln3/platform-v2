"""Legacy operator-side LLM proposals for governed brand entity resolution.

The policy-driven runtime lives in :mod:`domain.knowledge_evolution`: it may adopt
a clearly labelled ``model_inferred`` decision for one request without mutating the
master.  This module remains the offline review helper.  An operator can send
unresolved names, answer snippets, comparison scope, and the current master to an
LLM; the result stays ``requires_human_review=True`` until evidence is reviewed and
a new versioned release is published.

This separation is intentional.  Exact alias lookup is reliable for applying an
approved decision, but cannot *make* the decision.  Conversely, an LLM can reason
about context and corporate relationships, but cannot silently become a permanent
cross-project source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .entities import EntityMaster

PROMPT_VERSION = "brand-entity-resolution-v2"
SYSTEM_MESSAGE = (
    "你是品牌实体治理审查助手。你需要判断真实世界主体关系与特定评测场景的竞品资格，"
    "但你只能提交候选决策，不能把不确定推断写成事实。"
)

_DECISIONS = {"merge_existing", "create_entity", "keep_separate", "exclude", "ambiguous"}
_ENTITY_TYPES = {"company", "product", "tool", "institution", "unknown"}
_RELATIONSHIPS = {
    "same_legal_entity",
    "official_abbreviation",
    "english_name",
    "historical_name",
    "trade_name",
    "product_of",
    "business_unit_of",
    "subsidiary_of",
    "sibling_under_parent",
    "brand_family_member",
    "independent",
    "non_vendor",
    "uncertain",
}


class ResolutionError(ValueError):
    """The proposal request or LLM response is incomplete or unauditable."""


def _candidate_payload(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        observed_name = str(candidate.get("observed_name") or "").strip()
        if not observed_name or observed_name in seen:
            raise ResolutionError("candidate observed_name must be non-empty and unique")
        seen.add(observed_name)
        raw_contexts = candidate.get("contexts") or []
        if not isinstance(raw_contexts, Sequence) or isinstance(raw_contexts, str | bytes):
            raise ResolutionError(f"contexts must be a list for {observed_name!r}")
        contexts = [str(value).strip()[:800] for value in raw_contexts if str(value).strip()][:4]
        output.append(
            {
                "observed_name": observed_name,
                "answer_mentions": int(candidate.get("answer_mentions") or 0),
                "contexts": contexts,
            }
        )
    if not output:
        raise ResolutionError("at least one candidate is required")
    return output


def build_resolution_prompt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    master: EntityMaster,
    comparison_scope: str,
) -> str:
    """Render the full semantic-resolution prompt, including approved master data."""

    candidate_payload = _candidate_payload(candidates)
    catalog = [
        {
            "entity_id": record.entity_id,
            "canonical_name": record.canonical_name,
            "aliases": list(record.aliases),
            "alias_relationships": {
                alias: master.relationship_for(alias) for alias in record.aliases
            },
            "entity_type": record.entity_type,
            "brand_level": record.brand_level,
            "parent_brand": record.parent_brand,
            "industry_fit": record.industry_fit,
            "competitor_scopes": list(record.competitor_scopes),
            "competitor_eligible": record.competitor_eligible,
            "review_status": record.review_status,
        }
        for record in master.entities
    ]
    output_contract = {
        "prompt_version": PROMPT_VERSION,
        "decisions": [
            {
                "observed_name": "必须逐字复制输入名称",
                "decision": "merge_existing|create_entity|keep_separate|exclude|ambiguous",
                "matched_entity_id": "merge_existing 时必填，否则为 null",
                "canonical_name": "建议的展示名；ambiguous 时为 null",
                "entity_type": "company|product|tool|institution|unknown",
                "relationship": (
                    "same_legal_entity|official_abbreviation|english_name|historical_name|"
                    "trade_name|product_of|business_unit_of|subsidiary_of|"
                    "sibling_under_parent|brand_family_member|independent|non_vendor|uncertain"
                ),
                "competitor_eligible_for_scope": True,
                "applicable_scopes": ["适用的具体市场/场景"],
                "confidence": 0.0,
                "context_evidence": ["只摘录输入语境中支持判断的短语"],
                "reasoning": "区分主体同一性与竞品资格，简述理由",
                "external_verification_needed": True,
            }
        ],
    }
    policy = "\n".join(
        [
            "1. 将“是不是同一品牌实体”和“在当前场景是否构成竞品”分开判断；两者不得互相替代。",
            "2. 禁止仅凭字符串包含、共同前后缀、编辑距离或名称相似就合并。"
            "需要结合回答语境、母子品牌/集团关系、产品归属和业务角色。",
            "3. 品牌家族口径可把集团名、官方简称和明确的业务线品牌归到同一 "
            "canonical_name，但 relationship 必须精确区分同一法人、简称、曾用名、"
            "产品、业务线、子公司、兄弟公司和其他家族成员；品牌归并不等于同一法人。",
            "4. 产品品牌只有在用户实际比较产品/方案时才可作为竞品；开源工具、政府"
            "机构、研究所、标准、平台名称和泛化技术词默认不进入公司竞品榜。",
            "5. “数字认证”一类名称可能既是通用概念也是公司简称；“新大陆”一类名称"
            "也可能脱离公司语境。必须查看上下文是否把它作为厂商、是否出现股票代码、"
            "产品角色或完整主体名。",
            "6. 已存在于主数据的实体优先使用 merge_existing；不得臆造 entity_id。"
            "review_status=pending 的记录只能作为待审候选，不能视作正式竞品资格；"
            "无法排除两个合理主体时必须选 ambiguous。",
            "7. 上下文只能证明“回答如何称呼它”，不能证明工商关系或市场资格。涉及"
            "外部事实时 external_verification_needed 必须为 true，并由人工查公司官网、"
            "公告或监管披露。",
            "8. confidence 是本次候选判断的置信度，不是事实真伪概率。即使高置信度，"
            "输出仍只是待审核提案。",
            "9. 每个输入候选必须且只能输出一次，顺序与输入一致。只输出 JSON，不要 "
            "Markdown 或额外说明。",
        ]
    )
    return f"""请审查下面的品牌候选。
当前榜单聚合层级是“{master.aggregation_level}”，评测场景是“{comparison_scope}”。

你必须遵守以下规则：
{policy}

SiliconIndex 实体主数据（逐项查看 review_status，pending 不等于已审核）：
{json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))}

待审候选及回答语境：
{json.dumps(candidate_payload, ensure_ascii=False, separators=(",", ":"))}

输出契约示意：
{json.dumps(output_contract, ensure_ascii=False, separators=(",", ":"))}
"""


def parse_resolution_response(
    content: str,
    *,
    candidates: Sequence[Mapping[str, Any]],
    master: EntityMaster,
) -> dict[str, Any]:
    """Validate an LLM proposal and bind it to the exact request/master revision."""

    requested_rows = _candidate_payload(candidates)
    requested = [row["observed_name"] for row in requested_rows]
    contexts_by_name = {
        str(row["observed_name"]): [str(value) for value in row["contexts"]]
        for row in requested_rows
    }
    try:
        document = json.loads(content or "")
    except ValueError as exc:
        raise ResolutionError(f"bad_json: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("decisions"), list):
        raise ResolutionError("bad_shape: decisions list is required")
    if document.get("prompt_version") != PROMPT_VERSION:
        raise ResolutionError("bad_prompt_version")
    rows = document["decisions"]
    observed = [str(row.get("observed_name") or "") for row in rows if isinstance(row, dict)]
    if len(rows) != len(requested) or observed != requested:
        raise ResolutionError("candidate_set_or_order_mismatch")

    by_id = {record.entity_id: record for record in master.entities}
    validated: list[dict[str, Any]] = []
    for row in rows:
        decision = str(row.get("decision") or "")
        entity_type = str(row.get("entity_type") or "")
        relationship = str(row.get("relationship") or "")
        if decision not in _DECISIONS:
            raise ResolutionError(f"invalid_decision:{decision}")
        if entity_type not in _ENTITY_TYPES:
            raise ResolutionError(f"invalid_entity_type:{entity_type}")
        if relationship not in _RELATIONSHIPS:
            raise ResolutionError(f"invalid_relationship:{relationship}")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ResolutionError("invalid_confidence") from exc
        if not 0 <= confidence <= 1:
            raise ResolutionError("invalid_confidence")

        matched_id = row.get("matched_entity_id")
        canonical_name = row.get("canonical_name")
        if decision == "merge_existing":
            if not isinstance(matched_id, str) or matched_id not in by_id:
                raise ResolutionError("merge_existing_requires_known_entity_id")
            expected_name = by_id[matched_id].canonical_name
            if canonical_name != expected_name:
                raise ResolutionError("canonical_name_does_not_match_master")
        elif matched_id is not None:
            raise ResolutionError("matched_entity_id_only_allowed_for_merge_existing")
        if decision == "ambiguous" and canonical_name is not None:
            raise ResolutionError("ambiguous_canonical_name_must_be_null")
        if decision != "ambiguous" and not str(canonical_name or "").strip():
            raise ResolutionError("canonical_name_required")

        eligibility = row.get("competitor_eligible_for_scope")
        if eligibility is not None and not isinstance(eligibility, bool):
            raise ResolutionError("invalid_competitor_eligibility")
        scopes = row.get("applicable_scopes") or []
        evidence = row.get("context_evidence") or []
        if not isinstance(scopes, list) or not all(isinstance(value, str) for value in scopes):
            raise ResolutionError("invalid_applicable_scopes")
        if not isinstance(evidence, list) or not all(isinstance(value, str) for value in evidence):
            raise ResolutionError("invalid_context_evidence")
        observed_name = str(row["observed_name"])
        if any(
            excerpt.strip()
            and not any(excerpt.strip() in context for context in contexts_by_name[observed_name])
            for excerpt in evidence
        ):
            raise ResolutionError("context_evidence_not_in_input")
        external_verification = row.get("external_verification_needed")
        if not isinstance(external_verification, bool):
            raise ResolutionError("invalid_external_verification_flag")
        if decision == "create_entity" and external_verification is not True:
            raise ResolutionError("new_entity_requires_external_verification")
        if decision == "ambiguous" and (eligibility is not None or relationship != "uncertain"):
            raise ResolutionError("ambiguous_requires_uncertain_null_eligibility")
        if decision == "exclude" and (eligibility is not False or relationship != "non_vendor"):
            raise ResolutionError("exclude_requires_non_vendor_false_eligibility")
        reasoning = str(row.get("reasoning") or "").strip()
        if not reasoning:
            raise ResolutionError("reasoning_required")
        validated.append(
            {
                **row,
                "confidence": confidence,
                "reasoning": reasoning,
                "requires_human_review": True,
            }
        )

    return {
        "schema_version": "brand-entity-resolution-proposal-v1",
        "prompt_version": PROMPT_VERSION,
        "master_revision": master.revision,
        "aggregation_level": master.aggregation_level,
        "requires_human_review": True,
        "decisions": validated,
    }


def resolve_candidates_with_llm(
    client: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    master: EntityMaster,
    comparison_scope: str,
    model: str,
) -> dict[str, Any]:
    """Ask an OpenAI-compatible chat client for proposals; never mutate master data."""

    prompt = build_resolution_prompt(
        candidates,
        master=master,
        comparison_scope=comparison_scope,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            temperature=1.0 if model.strip().lower() == "moonshot-kimi-k3" else 0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 - adapter/network/model failures share one boundary
        raise ResolutionError(f"llm_error:{type(exc).__name__}") from exc
    return parse_resolution_response(content, candidates=candidates, master=master)


__all__ = [
    "PROMPT_VERSION",
    "ResolutionError",
    "build_resolution_prompt",
    "parse_resolution_response",
    "resolve_candidates_with_llm",
]
