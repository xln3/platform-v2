"""Version two semantic-decision domain.

This package intentionally has no database, workflow, HTTP, or model-SDK
dependency.  Adapters may propose structured outputs, but acceptance remains a
domain decision made by the validators and adjudicator exported here.
"""

from domain.analysis.v2.adjudication import (
    AdjudicationRequest,
    DecisionAdjudication,
    adjudicate_decision,
)
from domain.analysis.v2.decision_models import (
    DecisionMethod,
    DecisionStatus,
    SemanticDecisionAttempt,
    SemanticDecisionRecord,
)
from domain.analysis.v2.decision_task_loader import (
    DecisionTaskRegistry,
    load_builtin_judge_policies,
    load_builtin_task_definitions,
)
from domain.analysis.v2.decision_task_schema import (
    DecisionMethodPolicy,
    DecisionTaskDefinition,
    JudgePolicyDefinition,
)
from domain.analysis.v2.request_builder import (
    build_answer_semantic_workflow_request,
    instantiate_decision_task_request,
)

__all__ = [
    "AdjudicationRequest",
    "DecisionAdjudication",
    "DecisionMethod",
    "DecisionMethodPolicy",
    "DecisionStatus",
    "DecisionTaskDefinition",
    "DecisionTaskRegistry",
    "JudgePolicyDefinition",
    "SemanticDecisionAttempt",
    "SemanticDecisionRecord",
    "adjudicate_decision",
    "build_answer_semantic_workflow_request",
    "instantiate_decision_task_request",
    "load_builtin_judge_policies",
    "load_builtin_task_definitions",
]
