from __future__ import annotations

import copy

import pytest

from domain.metrics.customer import metric_catalog
from domain.metrics.v2.definition_loader import load_definitions
from domain.metrics.v2.definition_schema import (
    DefinitionValidationError,
    validate_metric_definition,
)
from domain.metrics.v2.legacy_disposition import (
    load_legacy_dispositions,
    validate_legacy_catalog,
)


def _valid_definition() -> dict[str, object]:
    return {
        "name": "example_rate_v2",
        "version": "2.0.0",
        "unit_type": "answer",
        "focal_entity_required": True,
        "outcome_source": "hybrid",
        "query_predicate": {"all": [{"query_has_lens": "ai_recommendation"}]},
        "required_semantic_capabilities": [
            {
                "name": "substantive_entity_mention",
                "task_ref": "substantive-entity-mention@2.0.0",
                "accepted_status": "ready",
            }
        ],
        "required_event_types": ["entity_mention"],
        "outcome": {
            "binary_outcome": {
                "event_exists": {
                    "type": "entity_mention",
                    "subject": "$focal_entity",
                    "where": {"substantive": True},
                }
            }
        },
        "missing_policy": "unknown_if_required_analysis_unready",
        "default_aggregation": "query_macro",
    }


def test_version_control_registry_loads_core_protocols_with_stable_hashes() -> None:
    first = load_definitions()
    second = load_definitions()
    assert first.definition_set_hash == second.definition_set_hash
    assert len(first.all()) >= 20
    organic = first.get("ai_recommendation_organic_mention_rate_v2", "2.0.0")
    assert organic.default_aggregation.value == "query_macro"
    assert len(organic.definition_hash) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python_expression", "__import__('os').system('id')"),
        ("sql", "select * from analytics.answer"),
        ("eval", "answer.mentioned"),
    ],
)
def test_executable_or_unknown_top_level_fields_are_rejected(field: str, value: str) -> None:
    raw = _valid_definition()
    raw[field] = value
    with pytest.raises(DefinitionValidationError, match="unknown fields"):
        validate_metric_definition(raw)


def test_unknown_dsl_node_and_event_field_fail_closed() -> None:
    raw = _valid_definition()
    raw["query_predicate"] = {"python_eval": "True"}
    with pytest.raises(DefinitionValidationError, match="unknown DSL node"):
        validate_metric_definition(raw)
    raw = _valid_definition()
    raw["outcome"] = {
        "binary_outcome": {
            "event_exists": {"type": "entity_mention", "where": {"typo_field": True}}
        }
    }
    with pytest.raises(DefinitionValidationError, match="unknown event fields"):
        validate_metric_definition(raw)


def test_declared_hash_drift_and_unpublished_task_reference_fail_startup() -> None:
    raw = _valid_definition()
    raw["definition_hash"] = "0" * 64
    with pytest.raises(DefinitionValidationError, match="hash drift"):
        validate_metric_definition(raw)
    registry = load_definitions()
    with pytest.raises(DefinitionValidationError, match="unpublished tasks"):
        registry.validate_published_dependencies(set())
    published = {
        requirement.task_ref
        for definition in registry.all()
        for requirement in definition.required_semantic_capabilities
    }
    published.update(
        task for definition in registry.all() for task in definition.decision_task_refs
    )
    registry.validate_published_dependencies(published)


def test_metric_definition_hash_changes_for_semantic_change() -> None:
    raw = _valid_definition()
    first = validate_metric_definition(raw)
    changed = copy.deepcopy(raw)
    changed["outcome"] = {
        "binary_outcome": {
            "event_exists": {
                "type": "entity_mention",
                "subject": "$focal_entity",
                "where": {"substantive": True, "mention_role": "asserted_body"},
            }
        }
    }
    assert validate_metric_definition(changed).definition_hash != first.definition_hash


def test_every_existing_customer_catalog_metric_has_one_v2_disposition() -> None:
    dispositions = load_legacy_dispositions()
    validate_legacy_catalog((item.code for item in metric_catalog()), dispositions)
    assert dispositions["mention_rate"] == "legacy"
    assert dispositions["geo_visibility_index"] == "experimental"
