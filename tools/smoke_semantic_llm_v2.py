"""Synthetic, secret-safe smoke check for the Metrics V2 semantic judge.

The command makes one real gateway request only when the configured daily
budget is positive and a credential is available.  It never reads customer
rows and never prints the synthetic prompt, API key, or raw provider response.
"""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

from geo_platform.config import get_settings

from domain.analysis.v2 import load_builtin_task_definitions
from domain.analysis.v2.candidates import Candidate, CandidateSet
from workflows.activities.semantic_judge_llm import (
    FrozenSemanticSource,
    SemanticJudgeFailure,
    config_from_settings,
    execute_semantic_judge,
)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _status_for_failure(code: str) -> int | str | None:
    if code == "llm_api_auth_rejected":
        return "401_or_403"
    if code == "llm_api_rate_limited":
        return 429
    if code == "llm_api_timeout":
        return 408
    if code == "llm_api_upstream_unavailable":
        return "5xx"
    if code in {"llm_api_request_rejected", "llm_api_response_format_unsupported"}:
        return "non_200"
    return None


def run() -> dict[str, Any]:
    settings = get_settings()
    config = config_from_settings(settings)
    summary: dict[str, Any] = {
        "schema_version": "semantic-llm-v2-smoke-v1",
        "provider": config.provider,
        "model": config.model,
        "model_revision": config.model_revision,
        "synthetic_only": True,
    }
    if settings.semantic_decision_daily_budget <= 0:
        return summary | {
            "upstream_http_status": None,
            "attempt_validation": "error",
            "reason_codes": ["llm_api_budget_exhausted"],
        }
    if not config.api_key:
        return summary | {
            "upstream_http_status": None,
            "attempt_validation": "error",
            "reason_codes": ["llm_api_auth_missing"],
        }

    task = load_builtin_task_definitions().get("substantive-entity-mention@2.0.0")
    synthetic_query = "请推荐一家示例网络安全公司"
    synthetic_answer = "示例品牌提供网络安全服务，适合本次合成测试。"
    candidate_set = CandidateSet(
        candidates=(
            Candidate(
                candidate_id="brand_synthetic",
                candidate_type="brand",
                labels=("示例品牌",),
            ),
        ),
        source_ref="synthetic://semantic-llm-v2-smoke/candidates",
        source_hash=_digest("semantic-llm-v2-smoke-candidates-v1"),
    )
    source = FrozenSemanticSource(
        source_ref="synthetic://semantic-llm-v2-smoke/answer",
        source_kind="answer",
        source_text=synthetic_answer,
        source_text_hash=_digest(synthetic_answer),
        related_query_text=synthetic_query,
        query_text_hash=_digest(synthetic_query),
        answer_content_hash=_digest(synthetic_answer),
    )
    # ``replace`` guarantees this tool cannot accidentally mutate or serialize
    # the settings object containing the credential.
    smoke_config = replace(config, max_retries=min(config.max_retries, 1))
    try:
        result = execute_semantic_judge(
            config=smoke_config,
            task=task,
            source=source,
            subject_ref={
                "answer_pub_id": "ans_synthetic",
                "entity_id": "brand_synthetic",
            },
            candidate_set=candidate_set,
        )
    except SemanticJudgeFailure as failure:
        return summary | {
            "upstream_http_status": _status_for_failure(failure.code),
            "attempt_validation": "error",
            "reason_codes": [failure.code],
        }
    return summary | {
        "upstream_http_status": 200,
        "attempt_validation": "valid",
        "reason_codes": ["llm_api_success"],
        "transport_mode": result.transport_mode,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "request_payload_hash": result.request_payload_hash,
        "response_payload_hash": result.response_payload_hash,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
