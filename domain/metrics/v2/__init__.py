"""Pure-domain V2 metric protocol and deterministic snapshot calculation."""

from .canonical_hash import (
    CANONICALIZATION_VERSION,
    canonical_hash,
    canonical_json,
    canonical_set_hash,
)
from .definition_loader import DefinitionRegistry, load_definition, load_definitions
from .definition_schema import MetricDefinition, validate_metric_definition
from .evaluator import MetricEvaluator
from .legacy_disposition import load_legacy_dispositions, validate_legacy_catalog
from .models import (
    DecisionMethod,
    DecisionStatus,
    EligibilityStatus,
    EvaluationInput,
    MetricContribution,
    MetricDesignCellContribution,
    MetricEvaluation,
    MetricQueryContribution,
    MetricSnapshot,
    MetricSnapshotSet,
    MetricSnapshotState,
    SemanticCapabilityStatus,
    SemanticDecisionFact,
)
from .query_context import (
    AnalysisLens,
    BrandStructureType,
    ClassificationState,
    ExposureRole,
    QueryContextFact,
    RequestedOperation,
    derive_exposure_role,
    derive_query_key,
    normalize_query_text,
)
from .semantic_events import (
    AnswerSemanticEvent,
    AnswerSemanticManifest,
    CapabilityAnalysis,
    CapabilityStatus,
    SemanticEventType,
)
from .snapshot_engine import DesignCoordinates, MetricSnapshotEngine, SnapshotBuildRequest
from .weighting import CalibrationErrorArtifact, WeightingInput

__all__ = [
    "CANONICALIZATION_VERSION",
    "AnswerSemanticEvent",
    "AnswerSemanticManifest",
    "AnalysisLens",
    "BrandStructureType",
    "CapabilityAnalysis",
    "CapabilityStatus",
    "DefinitionRegistry",
    "DesignCoordinates",
    "DecisionMethod",
    "DecisionStatus",
    "ClassificationState",
    "EligibilityStatus",
    "EvaluationInput",
    "ExposureRole",
    "MetricDefinition",
    "MetricContribution",
    "MetricDesignCellContribution",
    "MetricEvaluation",
    "MetricEvaluator",
    "MetricSnapshot",
    "MetricSnapshotEngine",
    "MetricSnapshotSet",
    "MetricSnapshotState",
    "MetricQueryContribution",
    "QueryContextFact",
    "RequestedOperation",
    "SnapshotBuildRequest",
    "SemanticCapabilityStatus",
    "SemanticDecisionFact",
    "SemanticEventType",
    "canonical_hash",
    "canonical_json",
    "canonical_set_hash",
    "derive_exposure_role",
    "derive_query_key",
    "CalibrationErrorArtifact",
    "load_definition",
    "load_definitions",
    "load_legacy_dispositions",
    "normalize_query_text",
    "validate_legacy_catalog",
    "validate_metric_definition",
    "WeightingInput",
]
