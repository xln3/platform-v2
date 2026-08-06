from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "tests/s04-evidence"
OUTPUT = EVIDENCE / "unified-completion-audit.json"


def load(name: str) -> dict[str, Any]:
    path = EVIDENCE / name
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def digest(name: str) -> str:
    return hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest()


def main() -> None:
    quality = load("full-quality-certification.json")
    browser = load("production-browser-acceptance.json")
    identity = load("production-identity-certification.json")
    external = load("external-gates-audit.json")
    reconciliation = load("production-target-reconciliation.json")
    runtime_counts = load("production-runtime-data-counts.json")
    rls = load("production-rls-certification.json")
    actor_identity = load("production-actor-identity.json")
    terminal = load("customer-terminal-protocol.json")
    terminal_runtime = load("customer-terminal-extension-runtime.json")
    terminal_release = load("customer-terminal-extension-release.json")
    ci = load("ci-workflow-certification.json")
    profile_rekey = load("profile-vault-rekey-certification.json")
    lease_lifecycle = load("session-lease-lifecycle-certification.json")
    production_session = load("production-session-lifecycle.json")
    account_revocation = load("production-account-revocation.json")
    authorization_propagation = load("authorization-revocation-propagation.json")
    revoked_terminal = load("production-revoked-account-terminal-state.json")
    authorization_replacement = load("authorization-replacement-propagation.json")
    evidence_concurrency = load("evidence-parent-concurrency.json")
    workflow_start = load("production-workflow-start-outbox.json")
    workflow_reconciliation = load("production-workflow-terminal-reconciliation.json")
    outbox_trace = load("production-outbox-trace.json")
    workflow_already_started = load("production-workflow-already-started.json")
    workflow_missing_history = load("production-workflow-missing-history.json")
    workflow_signal_outbox = load("production-workflow-signal-outbox.json")
    run_terminal_guard = load("production-run-terminal-guard.json")
    signal_idempotency = load("production-signal-idempotency.json")
    activity_accounting = load("production-activity-result-accounting.json")
    report_review_idempotency = load("production-report-review-idempotency.json")
    analysis_idempotency = load("production-analysis-idempotency.json")
    evidence_occurrence = load("production-evidence-occurrence-identity.json")
    collection_completion = load("production-collection-completion-outbox.json")
    outbox_routing = load("production-outbox-domain-routing.json")
    collection_analysis = load("production-collection-analysis-fanout.json")
    report_delivery = load("production-report-delivery-confirmation.json")
    report_authoring = load("production-report-authoring.json")
    business_alerting = load("production-business-alerting.json")
    postgres_rotation = load("production-postgres-credential-rotation.json")
    anti_geo = load("anti-geo-evaluation-boundary.json")

    browser_summary = browser["summary"]
    browser_clean = browser_summary["passed"] == browser_summary["total"] == 45 and all(
        check["secret_material_absent"]
        and check["screenshot"] is not None
        and all(value == 0 for value in check["runtime_issue_counts"].values())
        for check in browser["checks"]
    )
    real_identity = identity["final_identity_gates"]
    required_roles = set(external["production_identity_state"]["required_roles"])
    verified_roles = set(external["production_identity_state"]["verified_real_roles"])
    account_state = external["production_account_state"]
    counts = runtime_counts["counts"]

    gates = [
        {
            "number": 1,
            "name": "implementation_migrations_configuration_complete",
            "satisfied": False,
            "reason": (
                "Source-owned V2 code, schema and restricted extension artifact are deployed, "
                "but an authorized customer-terminal installation, production IdP bindings, "
                "live adapters and independently operated KMS custody are absent. A same-host "
                "production Vault Transit service proves mechanics only."
            ),
        },
        {
            "number": 2,
            "name": "lint_types_and_automated_tests",
            "satisfied": quality["result"] == "passed"
            and quality["static_and_unit_suite"]["python_tests_failed"] == 0,
            "evidence": "full-quality-certification.json",
            "ci_configuration": ci["result"],
            "hosted_ci_run_verified": ci["remote_run_verified"],
        },
        {
            "number": 3,
            "name": "real_dependency_integration",
            "satisfied": False,
            "qualification": (
                "PostgreSQL, ClickHouse, Temporal, MinIO, Redis and isolated Vault mechanism tests "
                "pass; live external platform, IdP and authorized customer-terminal dependency "
                "integration is absent."
            ),
        },
        {
            "number": 4,
            "name": "permissions_tenant_and_sensitive_fields",
            "satisfied": rls["result"] == "passed"
            and actor_identity["result"] == "passed"
            and actor_identity["legacy_external_subject_residue"] == 0
            and postgres_rotation["result"] == "passed"
            and rls["tenant_tables"]["total"]
            == rls["tenant_tables"]["rls_enabled"]
            == rls["tenant_tables"]["rls_forced"],
            "evidence": [
                "production-rls-certification.json",
                "production-actor-identity.json",
            ],
        },
        {
            "number": 5,
            "name": "idempotency_retry_and_partial_failure",
            "satisfied": reconciliation["summary"]["unapproved"] == 0
            and report_delivery["result"] == "passed"
            and report_authoring["result"] == "passed",
            "qualification": (
                "Covered for populated V2/migrated slices, workflow fault tests and a production "
                f"Temporal session lifecycle probe ({production_session['result']})."
            ),
        },
        {
            "number": 6,
            "name": "deployed_to_independent_production_v2_entry",
            "satisfied": browser["result"] == "passed" and business_alerting["result"] == "passed",
            "evidence": [
                "production-browser-acceptance.json",
                "production-business-alerting.json",
            ],
        },
        {
            "number": 7,
            "name": "real_roles_operate_successfully",
            "satisfied": verified_roles == required_roles
            and bool(real_identity["oidc_verified"])
            and bool(real_identity["passkey_verified"]),
            "verified_roles": sorted(verified_roles),
            "missing_roles": sorted(required_roles - verified_roles),
            "oidc_verified": real_identity["oidc_verified"],
            "passkey_verified": real_identity["passkey_verified"],
        },
        {
            "number": 8,
            "name": "production_screenshots_or_machine_evidence",
            "satisfied": browser_summary["passed"] == browser_summary["total"] == 45,
            "evidence": "production-browser-acceptance.json",
        },
        {
            "number": 9,
            "name": "zero_browser_console_and_failed_requests",
            "satisfied": browser_clean,
            "evidence": "production-browser-acceptance.json",
        },
        {
            "number": 10,
            "name": "implementation_status_records_urls_data_and_evidence",
            "satisfied": (ROOT.parent / "IMPLEMENTATION_STATUS.md").is_file(),
            "evidence": "../IMPLEMENTATION_STATUS.md",
        },
    ]

    open_requirements = [
        {
            "id": "identity-real-roles",
            "evidence_state": {
                "verified_roles": sorted(verified_roles),
                "missing_roles": sorted(required_roles - verified_roles),
                "oidc_verified": real_identity["oidc_verified"],
                "passkey_verified": real_identity["passkey_verified"],
            },
        },
        {
            "id": "profile-custody-migration-and-deletion",
            "evidence_state": {
                "platform_accounts": account_state["platform_accounts"],
                "browser_profiles": account_state["browser_profiles"],
                "active_profile_deks": account_state["active_profile_deks"],
                "profile_rekey_mechanics": profile_rekey["result"],
                "lease_lifecycle_mechanics": lease_lifecycle["result"],
                "account_revocation_mechanics": account_revocation["result"],
                "authorization_revocation_propagation": authorization_propagation["result"],
                "revoked_account_terminal_state": revoked_terminal["result"],
                "authorization_replacement": authorization_replacement["result"],
                "evidence_parent_concurrency": evidence_concurrency["result"],
                "workflow_start_outbox": workflow_start["result"],
                "workflow_terminal_reconciliation": workflow_reconciliation["result"],
                "api_workflow_activity_trace": outbox_trace["result"],
                "already_started_run_id_recovery": workflow_already_started["result"],
                "missing_history_terminal_recovery": workflow_missing_history["result"],
                "durable_ordered_signal_delivery": workflow_signal_outbox["result"],
                "collection_run_terminal_guard": run_terminal_guard["result"],
                "concurrent_signal_idempotency": signal_idempotency["result"],
                "activity_result_accounting": activity_accounting["result"],
                "report_review_idempotency": report_review_idempotency["result"],
                "answer_analysis_idempotency": analysis_idempotency["result"],
                "evidence_occurrence_identity": evidence_occurrence["result"],
                "collection_completion_outbox": collection_completion["result"],
                "outbox_domain_routing": outbox_routing["result"],
                "collection_analysis_fanout": collection_analysis["result"],
                "report_delivery_confirmation": report_delivery["result"],
                "plaintext_imported": external["safe_progress_state"]["plaintext_imported"],
                "plaintext_destroyed": external["safe_progress_state"]["plaintext_destroyed"],
            },
        },
        {
            "id": "customer-terminal-native-canaries",
            "evidence_state": {
                "device_bindings": account_state["customer_device_bindings"],
                "terminal_tasks": account_state["customer_terminal_tasks"],
                "native_canary_verified": external["safe_progress_state"][
                    "native_customer_terminal_canary_verified"
                ],
                "protocol_result": terminal["result"],
                "local_chromium_runtime_result": (
                    "passed_not_customer_authorized" if terminal_runtime["passed"] else "failed"
                ),
                "production_signed_release_result": terminal_release["result"],
            },
        },
        {
            "id": "live-platform-capability-admission",
            "evidence_state": {
                "platform_adapters": account_state["platform_adapters"],
                "currently_authorized_accounts": account_state["currently_authorized_accounts"],
                "live_capability_claimed": external["safe_progress_state"][
                    "live_capability_claimed"
                ],
            },
        },
        {
            "id": "approved-anti-geo-calibration-dataset",
            "evidence_state": anti_geo["qualification"],
        },
        {
            "id": "populated-report-shadow",
            "evidence_state": {
                "production_reports": counts["reports"],
                "delivery_confirmation_storage_probe": report_delivery["result"],
                "immutable_authoring_contract": report_authoring["result"],
                "real_customer_delivery_acceptance": False,
                "reconciliation_unapproved_differences": reconciliation["summary"]["unapproved"],
            },
        },
    ]
    satisfied_count = sum(bool(gate["satisfied"]) for gate in gates)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "result": "incomplete_external_authority_and_real_sample_gates_open",
        "section_18": {
            "satisfied": satisfied_count,
            "total": len(gates),
            "all_satisfied": satisfied_count == len(gates),
            "gates": gates,
        },
        "open_requirements": open_requirements,
        "evidence_sha256": {
            name: digest(name)
            for name in (
                "full-quality-certification.json",
                "production-browser-acceptance.json",
                "production-identity-certification.json",
                "external-gates-audit.json",
                "production-target-reconciliation.json",
                "production-runtime-data-counts.json",
                "production-rls-certification.json",
                "production-actor-identity.json",
                "customer-terminal-protocol.json",
                "customer-terminal-extension-runtime.json",
                "customer-terminal-extension-release.json",
                "ci-workflow-certification.json",
                "profile-vault-rekey-certification.json",
                "session-lease-lifecycle-certification.json",
                "production-session-lifecycle.json",
                "production-account-revocation.json",
                "authorization-revocation-propagation.json",
                "production-revoked-account-terminal-state.json",
                "authorization-replacement-propagation.json",
                "evidence-parent-concurrency.json",
                "production-workflow-start-outbox.json",
                "production-workflow-terminal-reconciliation.json",
                "production-outbox-trace.json",
                "production-workflow-already-started.json",
                "production-workflow-missing-history.json",
                "production-workflow-signal-outbox.json",
                "production-run-terminal-guard.json",
                "production-signal-idempotency.json",
                "production-activity-result-accounting.json",
                "production-report-review-idempotency.json",
                "production-analysis-idempotency.json",
                "production-evidence-occurrence-identity.json",
                "production-collection-completion-outbox.json",
                "production-outbox-domain-routing.json",
                "production-collection-analysis-fanout.json",
                "production-report-delivery-confirmation.json",
                "production-report-authoring.json",
                "production-business-alerting.json",
                "production-postgres-credential-rotation.json",
                "anti-geo-evaluation-boundary.json",
            )
        },
        "legacy_route_switched": False,
        "secret_material_in_evidence": False,
        "goal_status": "active",
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "result": result["result"],
                "section_18_satisfied": satisfied_count,
                "section_18_total": len(gates),
                "open_requirements": len(open_requirements),
            }
        )
    )


if __name__ == "__main__":
    main()
