"""Service-1 scope registration, quotation compliance, and release-state gates.

The module is deliberately pure so the API, Temporal worker, offline generator, and
tests all enforce the same rules.  A report may describe historical observations when
registration evidence is missing, but it can never be approved/signed as if the scope
had been frozen before sampling.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

SERVICE1_MODELS = ("doubao", "deepseek", "yiyan")
SERVICE1_REGIONS = ("北京", "上海")
SERVICE1_REPETITIONS = 2
SERVICE1_GROUPS = 3
SERVICE1_QUESTIONS_PER_GROUP = 4
SERVICE1_ANSWERS = 144

RELEASE_STATES = frozenset({"internal_review", "delivery_candidate", "approved_signed"})
GENERATABLE_STATES = frozenset({"internal_review", "delivery_candidate"})


def question_group_hash(questions: Sequence[str]) -> str:
    material = "\n".join(str(value).strip() for value in questions)
    return sha256(material.encode()).hexdigest()


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _service_number(group: Mapping[str, Any]) -> int | None:
    raw = group.get("service_number")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    appendix = group.get("quotation_appendix")
    if appendix == 2:
        return 1
    if appendix == 3:
        return 4
    return None


def _legacy_scope_label(groups: Sequence[Mapping[str, Any]]) -> str:
    asset_markers = ("资产", "攻击面", "暴露面", "漏洞", "影子")
    if groups and all(
        any(
            marker in " ".join([str(group.get("title") or ""), *group.get("questions", [])])
            for marker in asset_markers
        )
        for group in groups
    ):
        return "网空线三类资产治理场景"
    return "本次三组已测业务场景"


def resolve_scope_registration(
    *,
    snapshot: Mapping[str, Any],
    candidate_groups: Sequence[Mapping[str, Any]],
    answers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve an immutable pre-sampling scope, or an explicitly non-signable fallback."""

    registration = snapshot.get("service1_scope_registration")
    registration = registration if isinstance(registration, Mapping) else {}
    by_hash = {
        str(
            group.get("question_group_hash") or question_group_hash(group.get("questions", []))
        ): group
        for group in candidate_groups
    }
    raw_hashes = registration.get("group_hashes")
    registered_hashes = (
        [str(value) for value in raw_hashes]
        if isinstance(raw_hashes, Sequence) and not isinstance(raw_hashes, str | bytes)
        else []
    )
    selected = [by_hash[value] for value in registered_hashes if value in by_hash]

    reasons: list[str] = []
    if registration.get("schema_version") != "service1-scope-registration-v1":
        reasons.append("scope_not_preregistered")
    if len(registered_hashes) != SERVICE1_GROUPS or len(selected) != SERVICE1_GROUPS:
        reasons.append("three_registered_groups_required")
    if len(set(registered_hashes)) != len(registered_hashes):
        reasons.append("registered_groups_must_be_unique")
    if selected and any(
        len(group.get("questions", [])) != SERVICE1_QUESTIONS_PER_GROUP for group in selected
    ):
        reasons.append("four_question_texts_per_group_required")
    if selected and any(_service_number(group) != 1 for group in selected):
        reasons.append("service1_service4_boundary_unverified")
    frozen_at = _as_utc(registration.get("frozen_at"))
    if frozen_at is None:
        reasons.append("scope_freeze_time_missing")
    if not str(registration.get("selection_basis") or "").strip():
        reasons.append("scope_selection_basis_missing")
    if not str(registration.get("confirmed_by") or "").strip():
        reasons.append("scope_confirmer_missing")

    selected_questions = {
        str(question) for group in selected for question in group.get("questions", [])
    }
    first_sample = min(
        (
            capture
            for row in answers
            if str(row.get("query_text") or "") in selected_questions
            and (capture := _as_utc(row.get("capture_time"))) is not None
        ),
        default=None,
    )
    if frozen_at is not None and first_sample is not None and frozen_at > first_sample:
        reasons.append("scope_frozen_after_first_sample")

    registered = not reasons
    if not selected:
        # Historical fallback is independent of outcomes and completeness: it uses the
        # quotation order only.  It is descriptive and always fails the approval gate.
        selected = list(candidate_groups[:SERVICE1_GROUPS])
    scope_label = str(registration.get("scope_label") or "").strip()
    represents_overall = registration.get("represents_overall_brand") is True
    if not registered or not represents_overall:
        scope_label = scope_label or _legacy_scope_label(selected)
    return {
        "schema_version": "service1-scope-resolution-v1",
        "status": "registered" if registered else "historical_unregistered",
        "ready_for_approval": registered,
        "reasons": reasons,
        "selected_group_hashes": [
            str(group.get("question_group_hash") or question_group_hash(group.get("questions", [])))
            for group in selected
        ],
        "frozen_at": frozen_at,
        "first_sample_at": first_sample,
        "selection_basis": str(registration.get("selection_basis") or "").strip()
        or ("历史批次未预注册；仅按报价问题顺序重建已测范围，不作为正式选题证明"),
        "confirmed_by": str(registration.get("confirmed_by") or "").strip() or None,
        "represents_overall_brand": represents_overall if registered else False,
        "scope_label": scope_label,
    }


def assign_repeats(
    answers: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    """Assign repeat numbers in capture order and prove run-level independence."""

    cells: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in answers:
        cells[
            (
                str(row.get("query_text") or ""),
                str(row.get("model") or ""),
                str(row.get("region") or ""),
            )
        ].append(row)
    repeat_by_answer: dict[str, int] = {}
    reasons: list[str] = []
    for cell, rows in cells.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                _as_utc(row.get("capture_time")) or datetime.min.replace(tzinfo=UTC),
                str(row.get("pub_id") or row.get("answer_pub_id") or ""),
            ),
        )
        run_ids = [str(row.get("run_pub_id") or "").strip() for row in ordered]
        if len(ordered) != SERVICE1_REPETITIONS:
            reasons.append("repeat_count_invalid:" + "|".join(cell))
        if any(not value for value in run_ids) or len(set(run_ids)) != len(run_ids):
            reasons.append("repeat_run_independence_unproven:" + "|".join(cell))
        for repeat_no, row in enumerate(ordered, 1):
            answer_id = str(row.get("pub_id") or row.get("answer_pub_id") or "")
            if answer_id:
                repeat_by_answer[answer_id] = repeat_no
    return repeat_by_answer, sorted(set(reasons))


def quotation_gate(
    *,
    selected_groups: Sequence[Mapping[str, Any]],
    sample_rows: Sequence[Mapping[str, Any]],
    scope_registration: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Validate the complete Service-1 quotation matrix and provenance ledger."""

    reasons = list(scope_registration.get("reasons") or [])
    if len(selected_groups) != SERVICE1_GROUPS:
        reasons.append("three_business_question_groups_required")
    if any(
        len(group.get("questions", [])) != SERVICE1_QUESTIONS_PER_GROUP for group in selected_groups
    ):
        reasons.append("four_actual_questions_per_group_required")
    if any(_service_number(group) != 1 for group in selected_groups):
        reasons.append("service1_service4_boundary_unverified")

    expected_questions = [
        str(question) for group in selected_groups for question in group.get("questions", [])
    ]
    expected_cells = {
        (question, model, region)
        for question in expected_questions
        for model in SERVICE1_MODELS
        for region in SERVICE1_REGIONS
    }
    counts = Counter(
        (
            str(row.get("question") or row.get("query_text") or ""),
            str(row.get("platform") or row.get("model") or ""),
            str(row.get("region") or ""),
        )
        for row in sample_rows
    )
    if set(counts) != expected_cells or any(
        counts[cell] != SERVICE1_REPETITIONS for cell in expected_cells
    ):
        reasons.append("three_platform_two_region_two_repeat_matrix_incomplete")
    if len(sample_rows) != SERVICE1_ANSWERS:
        reasons.append("service1_144_samples_required")
    for field, reason in (
        ("sample_id", "sample_id_missing"),
        ("repeat_no", "repeat_number_missing"),
        ("capture_time", "capture_time_missing"),
        ("run_id", "run_id_missing"),
        ("response_text", "complete_answer_missing"),
    ):
        if any(row.get(field) in (None, "") for row in sample_rows):
            reasons.append(reason)
    if any(not row.get("account_id_masked") for row in sample_rows):
        reasons.append("region_account_ledger_missing")
    if any(not row.get("browser_instance") for row in sample_rows):
        reasons.append("browser_instance_ledger_missing")
    if any(not row.get("egress_region_gb") or not row.get("egress_audit") for row in sample_rows):
        reasons.append("egress_region_audit_missing")
    if any(not row.get("independent_repeat") for row in sample_rows):
        reasons.append("repeat_independence_unproven")
    if any(
        not row.get("answer_evidence") or not row.get("screenshot_evidence") for row in sample_rows
    ):
        reasons.append("primary_sample_evidence_incomplete")
    return not reasons, tuple(dict.fromkeys(reasons))


def release_state_label(status: str) -> str:
    return {
        "internal_review": "内部审核稿",
        "delivery_candidate": "客户交付候选稿",
        "approved_signed": "已批准签发版",
    }.get(status, "未知状态")


__all__ = [
    "GENERATABLE_STATES",
    "RELEASE_STATES",
    "SERVICE1_ANSWERS",
    "SERVICE1_GROUPS",
    "SERVICE1_MODELS",
    "SERVICE1_QUESTIONS_PER_GROUP",
    "SERVICE1_REGIONS",
    "SERVICE1_REPETITIONS",
    "assign_repeats",
    "question_group_hash",
    "quotation_gate",
    "release_state_label",
    "resolve_scope_registration",
]
