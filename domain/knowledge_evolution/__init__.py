"""Evidence-driven, domain-neutral knowledge evolution middleware.

The package deliberately contains no GEO API, database, brand, or SiliconIndex
assumptions.  Deployments provide repositories, model gateways, release storage,
and domain packs through the protocols exported here.
"""

from .client import KnowledgeClientError, KnowledgeHttpClient
from .contracts import (
    Decision,
    DecisionScope,
    GatewayResult,
    KnowledgeStatus,
    ModelPrompt,
    ObservationDraft,
    ReasoningPolicy,
    ReleaseRef,
    RuntimeRequest,
    RuntimeResponse,
)
from .events import KnowledgeEvent, event_envelope
from .gateway import GatewayError, OpenAICompatibleGateway
from .merge import MergeConflict, MergeResult, three_way_merge
from .registry import DomainPack, DomainRegistry
from .release import KnowledgeReleaseError, KnowledgeReleaseStore
from .runtime import ModelGateway, ReasoningEngine, ReasoningError, RuntimePersistence

__all__ = [
    "Decision",
    "DecisionScope",
    "DomainPack",
    "DomainRegistry",
    "GatewayResult",
    "GatewayError",
    "KnowledgeReleaseError",
    "KnowledgeReleaseStore",
    "KnowledgeClientError",
    "KnowledgeHttpClient",
    "KnowledgeStatus",
    "KnowledgeEvent",
    "MergeConflict",
    "MergeResult",
    "ModelGateway",
    "ModelPrompt",
    "ObservationDraft",
    "OpenAICompatibleGateway",
    "ReasoningEngine",
    "ReasoningError",
    "ReasoningPolicy",
    "ReleaseRef",
    "RuntimePersistence",
    "RuntimeRequest",
    "RuntimeResponse",
    "event_envelope",
    "three_way_merge",
]
