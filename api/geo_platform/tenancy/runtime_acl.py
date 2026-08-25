"""Single source of truth for production runtime database privileges.

The policy is intentionally closed-world: every managed relation, sequence and
function absent from this manifest must be inaccessible to ``geo_api`` and
``geo_worker``.  Provisioning, the head migration and verification all consume
these same immutable values.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

API_ROLE: Final = "geo_api"
WORKER_ROLE: Final = "geo_worker"
RUNTIME_ROLES: Final = (API_ROLE, WORKER_ROLE)
MANAGED_SCHEMAS: Final = (
    "platform",
    "analytics",
    "evidence",
    "reporting",
    "intelligence",
    "integration",
    "sop",
    "posting",
    "notification",
)
TABLE_ACTIONS: Final = ("SELECT", "INSERT", "UPDATE", "DELETE")
SEQUENCE_ACTIONS: Final = ("USAGE", "SELECT", "UPDATE")


@dataclass(frozen=True, slots=True)
class TableGrant:
    privileges: frozenset[str]
    update_columns: tuple[str, ...] = ()


def _names(schema: str, values: str) -> tuple[str, ...]:
    return tuple(f"{schema}.{name}" for name in values.split())


_PLATFORM_COMMON_READ = _names(
    "platform",
    """
    tenant customer project brand brand_alias brand_asset competitor query_group query_item
    query_variant variant_seed monitoring_config monitoring_config_version monitoring_schedule
    monitoring_schedule_event project_service_entitlement intake_profile intake_promo
    intake_trigger_question intake_invite client_goal client_profile_version
    asset_confirmation_version change_request account_authorization account_sla_policy
    platform_adapter platform_account browser_profile browser_fence capability_lease
    resource_registration resource_lease collection_run collection_task collection_campaign
    collection_campaign_target collection_campaign_materialization_batch
    collection_config_revision_v2 collection_config_target_v2 collection_primary_slot
    collection_sampling_leg collection_region collection_browser collection_phone_account
    collection_platform_account collection_account_event collection_surface_backfill_run
    collection_execution_plan_v2 collection_execution_partition_v2
    collection_execution_start_outbox_v2 collection_capability_registry_revision
    collection_capability_declaration collection_quota_registry_revision
    collection_quota_scope_policy collection_binding_revision_v2 collection_api_binding_v2
    collection_web_binding_v2 collection_app_binding_v2 collection_binding_capability
    collection_binding_resource collection_binding_quota_scope collection_submission_operation
    collection_submission_reconciliation_proof collection_resource_adoption
    collection_resource_capacity_unit collection_quota_bucket collection_quota_reservation
    collection_quota_reservation_effect collection_quota_ledger_event
    collection_execution_grant_v2 collection_api_execution_grant_v2
    collection_web_execution_grant_v2 collection_app_execution_grant_v2
    collection_execution_grant_resource collection_submission_request_manifest_v2
    collection_capture_truth_v2 collection_submission_dispatch_v2
    collection_submission_transition_evidence_v2 collection_capture_manifest_v2
    collection_observation_v2 collection_slot_outcome_v2 collection_analysis_admission_v2
    collection_governance_effect_v2 collection_governance_outbox_v2
    collection_query_retry_intent collection_query_execution_attempt
    collection_failure_knowledge analysis_job answer_library_catalog answer_retrieval_event
    answer_source_occurrence source_site source_url source_document source_fetch_attempt
    source_page_snapshot source_analysis_profile source_audit page_inspection
    page_inspection_finding page_evidence_span site_audit_suggestion
    disparagement_judgment disparagement_factcheck weighted_content_chunk
    weighted_content_chunk_review content_contribution_analysis content_strategy_analysis
    post_analysis_task post_analysis_item service2_corpus_batch service2_corpus_batch_run
    service2_corpus_batch_query service2_corpus_item service2_analysis_attempt
    service2_relation_finding service2_finding_review service2_fact_manifest
    service2_batch_event service2_model_call intervention_request revocation_request
    terminal_task
    """,
)

_API_IDENTITY_READ = _names(
    "platform",
    """
    app_user membership role permission role_permission oidc_identity_binding
    user_password_credential login_attempt browser_session device_binding session_lease
    session_event session_health_check credential_access_request credential_access_approval
    audit_log
    """,
)

_API_PLATFORM_WRITE = _names(
    "platform",
    """
    customer project brand brand_alias brand_asset competitor query_group query_item query_variant
    monitoring_config monitoring_config_version monitoring_schedule monitoring_schedule_event
    project_service_entitlement intake_profile intake_promo intake_trigger_question intake_invite
    client_goal client_profile_version asset_confirmation_version change_request
    account_authorization account_sla_policy platform_adapter platform_account browser_profile
    capability_lease collection_run collection_task collection_campaign
    collection_campaign_target collection_campaign_materialization_batch
    collection_config_revision_v2 collection_config_target_v2 collection_primary_slot
    collection_sampling_leg collection_region collection_browser collection_phone_account
    collection_platform_account collection_account_event collection_surface_backfill_run
    collection_query_retry_intent collection_query_execution_attempt collection_failure_knowledge
    analysis_job source_analysis_profile weighted_content_chunk_review post_analysis_task
    post_analysis_item service2_corpus_batch service2_corpus_batch_run
    service2_corpus_batch_query service2_corpus_item service2_analysis_attempt
    service2_relation_finding service2_finding_review service2_fact_manifest
    service2_batch_event service2_model_call intervention_request revocation_request terminal_task
    app_user membership oidc_identity_binding user_password_credential login_attempt
    browser_session device_binding session_lease session_event session_health_check
    credential_access_request credential_access_approval audit_log
    """,
)

_API_PLATFORM_DELETE = frozenset(
    _names(
        "platform",
        "brand brand_alias brand_asset competitor query_group query_item client_goal "
        "change_request intake_promo intake_trigger_question",
    )
)

_WORKER_PLATFORM_WRITE = _names(
    "platform",
    """
    monitoring_schedule monitoring_schedule_event project_service_entitlement
    platform_adapter platform_account browser_profile browser_fence capability_lease
    resource_lease collection_run collection_task collection_campaign
    collection_campaign_target collection_campaign_materialization_batch
    collection_config_revision_v2 collection_config_target_v2 collection_primary_slot
    collection_sampling_leg collection_region collection_browser collection_phone_account
    collection_platform_account collection_account_event collection_surface_backfill_run
    collection_execution_plan_v2 collection_execution_partition_v2
    collection_execution_start_outbox_v2 collection_query_retry_intent
    collection_query_execution_attempt collection_failure_knowledge analysis_job
    answer_retrieval_event answer_source_occurrence source_site source_url source_document
    source_fetch_attempt source_page_snapshot source_analysis_profile source_audit
    page_inspection page_inspection_finding page_evidence_span site_audit_suggestion
    disparagement_judgment disparagement_factcheck weighted_content_chunk
    weighted_content_chunk_review content_contribution_analysis content_strategy_analysis
    post_analysis_task post_analysis_item service2_corpus_batch service2_corpus_batch_run
    service2_corpus_batch_query service2_corpus_item service2_analysis_attempt
    service2_relation_finding service2_finding_review service2_fact_manifest
    service2_batch_event service2_model_call
    """,
)

_STAGE2_TABLES = _names(
    "platform",
    """
    collection_capability_registry_revision collection_capability_declaration
    collection_quota_registry_revision collection_quota_scope_policy
    collection_binding_revision_v2 collection_api_binding_v2 collection_web_binding_v2
    collection_app_binding_v2 collection_binding_capability collection_binding_resource
    collection_binding_quota_scope collection_submission_operation
    collection_submission_reconciliation_proof collection_resource_adoption
    collection_resource_capacity_unit collection_quota_bucket collection_quota_reservation
    collection_quota_reservation_effect collection_quota_ledger_event
    collection_execution_grant_v2 collection_api_execution_grant_v2
    collection_web_execution_grant_v2 collection_app_execution_grant_v2
    collection_execution_grant_resource
    """,
)

_STAGE3_TABLES = _names(
    "platform",
    """
    collection_submission_request_manifest_v2 collection_capture_truth_v2
    collection_submission_dispatch_v2 collection_submission_transition_evidence_v2
    collection_capture_manifest_v2 collection_observation_v2 collection_slot_outcome_v2
    collection_analysis_admission_v2 collection_governance_effect_v2
    collection_governance_outbox_v2
    """,
)

# S11 persists immutable execution-plan history behind SECURITY DEFINER
# entrypoints.  Runtime roles may inspect the rows but must never bypass the
# compare-and-swap/lineage checks with direct table writes.
_S11_EXECUTION_TABLES = _names(
    "platform",
    "collection_execution_plan_v2 collection_execution_partition_v2 "
    "collection_execution_start_outbox_v2",
)

_SOP_TABLES = _names(
    "sop",
    """
    project query_set query_item baseline_answer retrieval_insight evidence_item opportunity
    article article_version pre_publish_check publication index_observation retest_answer
    comparison experiment work_log
    """,
)
_POSTING_TABLES = _names("posting", "batch target event attribution")
_NOTIFICATION_TABLES = _names(
    "notification", "notice interaction delivery_command callback_replay audit_event"
)
_ANALYTICS_TABLES = _names(
    "analytics",
    """
    analysis_run anomaly_event answer answer_analysis answer_brand_extract
    answer_citation_relation citation_fact metric_daily metric_definition metric_trace
    run_comparison answer_agg_blind
    """,
)
_EVIDENCE_TABLES = _names(
    "evidence",
    """
    evidence_asset evidence_anchor evidence_relation evidence_snapshot evidence_diff
    evidence_package evidence_access_grant evidence_access_audit answer_share_artifact
    answer_share_verification_event
    """,
)
_REPORTING_TABLES = _names(
    "reporting",
    """
    report report_version report_component report_artifact report_evidence_reference
    report_frozen_fact report_event report_review report_comment report_delivery
    optimization_action effect_retest data_export formal_report_production formal_report_output
    """,
)
_INTELLIGENCE_TABLES = _names(
    "intelligence",
    """
    investigation author_identity domain_profile entity content_item content_version graph_edge
    similarity_edge propagation_event claim claim_occurrence claim_evidence source_independence
    detection_feature detection_score human_verdict appeal evaluation_dataset
    evaluation_dataset_case evaluation_run evaluation_case_result model_admission
    """,
)
_INTEGRATION_TABLES = _names(
    "integration", "outbox_event workflow_start_command workflow_signal_command"
)

_API_STAGE2_INSERT = frozenset(
    _names(
        "platform",
        """
        collection_capability_registry_revision collection_capability_declaration
        collection_quota_registry_revision collection_quota_scope_policy
        collection_binding_revision_v2 collection_api_binding_v2 collection_web_binding_v2
        collection_app_binding_v2 collection_binding_capability collection_binding_resource
        collection_binding_quota_scope collection_resource_adoption
        """,
    )
)
_WORKER_STAGE2_INSERT = frozenset(
    _names(
        "platform",
        """
        collection_submission_operation collection_resource_capacity_unit
        collection_quota_bucket collection_quota_reservation
        collection_quota_reservation_effect collection_quota_ledger_event
        collection_execution_grant_v2 collection_api_execution_grant_v2
        collection_web_execution_grant_v2 collection_app_execution_grant_v2
        collection_execution_grant_resource
        """,
    )
)

_API_STAGE2_UPDATE_COLUMNS = {
    "platform.collection_capability_registry_revision": (
        "lifecycle_state",
        "change_reason",
        "approved_by_pub_id",
        "frozen_at",
        "activated_at",
        "retired_at",
        "version",
        "updated_at",
    ),
    "platform.collection_capability_declaration": (
        "status",
        "production_allowed",
        "region_policy_revision",
        "required_resource_kinds_json",
        "observable_capture_fields_json",
        "product_version_constraints_json",
        "unsupported_reason",
        "alternative_suggestion",
        "version",
        "updated_at",
    ),
    "platform.collection_quota_registry_revision": (
        "lifecycle_state",
        "change_reason",
        "approved_by_pub_id",
        "frozen_at",
        "activated_at",
        "retired_at",
        "version",
        "updated_at",
    ),
    "platform.collection_quota_scope_policy": (
        "share_policy",
        "window_unit",
        "window_size",
        "window_timezone",
        "window_boundary_revision",
        "provider_window_code",
        "limit_units",
        "limit_source",
        "settlement_policy_revision",
        "lock_order_ordinal",
        "version",
        "updated_at",
    ),
    "platform.collection_binding_revision_v2": (
        "lifecycle_state",
        "lifecycle_reason",
        "activated_at",
        "suspended_at",
        "revoked_at",
        "superseded_at",
        "version",
        "updated_at",
    ),
    "platform.collection_resource_adoption": (
        "verification_state",
        "verified_by_pub_id",
        "verified_at",
        "adopted_at",
        "revoked_at",
        "state_reason",
        "version",
        "updated_at",
    ),
    "platform.resource_registration": (
        "display_mask",
        "capabilities_json",
        "region",
        "concurrency_limit",
        "state",
        "last_heartbeat_at",
        "project_id",
        "resource_schema_version",
        "resource_revision",
        "owner_gateway_kind",
        "owner_gateway_revision",
        "opaque_owner_handle",
        "attestation_revision",
        "route_policy_revision",
        "resource_fingerprint",
        "approved_at",
        "revoked_at",
        "version",
        "updated_at",
    ),
}
_WORKER_STAGE2_UPDATE_COLUMNS = {
    "platform.collection_resource_capacity_unit": (
        "capacity_state",
        "current_fencing_token",
        "last_heartbeat_at",
        "quarantined_at",
        "revoked_at",
        "state_reason",
        "version",
        "updated_at",
    ),
    "platform.collection_quota_bucket": (
        "reserved_units",
        "settled_consumed_units",
        "settled_unknown_units",
        "bucket_state",
        "fence_version",
        "version",
        "updated_at",
    ),
    "platform.collection_quota_reservation": (
        "reservation_state",
        "reserved_at",
        "finalized_at",
        "reconcile_after",
        "state_reason",
        "version",
        "updated_at",
    ),
    "platform.collection_quota_reservation_effect": (
        "effect_state",
        "state_reason",
        "settled_at",
        "released_at",
        "version",
        "updated_at",
    ),
    "platform.collection_execution_grant_v2": (
        "grant_state",
        "issued_at",
        "revoked_at",
        "revocation_reason",
        "version",
        "updated_at",
    ),
    "platform.resource_registration": (
        "state",
        "last_heartbeat_at",
        "revoked_at",
        "version",
        "updated_at",
    ),
    "platform.resource_lease": (
        "lease_state",
        "heartbeat_at",
        "expires_at",
        "released_at",
        "revoked_at",
        "reconciliation_reason",
        "version",
        "updated_at",
    ),
}


def _put(
    grants: dict[str, TableGrant],
    tables: tuple[str, ...],
    privileges: frozenset[str],
) -> None:
    for table in tables:
        previous = grants.get(table)
        merged = privileges | (previous.privileges if previous else frozenset())
        grants[table] = TableGrant(merged, previous.update_columns if previous else ())


def _build_table_policy(role: str) -> MappingProxyType[str, TableGrant]:
    grants: dict[str, TableGrant] = {}
    _put(grants, _PLATFORM_COMMON_READ, frozenset({"SELECT"}))
    if role == API_ROLE:
        _put(grants, _API_IDENTITY_READ, frozenset({"SELECT"}))
        _put(grants, _API_PLATFORM_WRITE, frozenset({"SELECT", "INSERT", "UPDATE"}))
        _put(grants, _ANALYTICS_TABLES, frozenset({"SELECT"}))
        for tables in (
            _EVIDENCE_TABLES,
            _REPORTING_TABLES,
            _INTELLIGENCE_TABLES,
            _INTEGRATION_TABLES,
            _SOP_TABLES,
            _POSTING_TABLES,
            _NOTIFICATION_TABLES,
        ):
            _put(grants, tables, frozenset({"SELECT", "INSERT", "UPDATE"}))
        grants["notification.callback_replay"] = TableGrant(
            frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
        )
        for table in _API_PLATFORM_DELETE:
            current = grants[table]
            grants[table] = TableGrant(current.privileges | {"DELETE"})
        for table in _API_STAGE2_INSERT:
            grants[table] = TableGrant(frozenset({"SELECT", "INSERT"}))
        for table, columns in _API_STAGE2_UPDATE_COLUMNS.items():
            current = grants.get(table, TableGrant(frozenset({"SELECT"})))
            grants[table] = TableGrant(current.privileges - {"UPDATE"}, columns)
    elif role == WORKER_ROLE:
        _put(grants, _WORKER_PLATFORM_WRITE, frozenset({"SELECT", "INSERT", "UPDATE"}))
        for tables in (
            _ANALYTICS_TABLES,
            _EVIDENCE_TABLES,
            _REPORTING_TABLES,
            _INTELLIGENCE_TABLES,
            _INTEGRATION_TABLES,
            _NOTIFICATION_TABLES,
        ):
            _put(grants, tables, frozenset({"SELECT", "INSERT", "UPDATE"}))
        _put(grants, _SOP_TABLES, frozenset({"SELECT"}))
        _put(grants, _POSTING_TABLES, frozenset({"SELECT"}))
        for table in _STAGE2_TABLES:
            grants[table] = TableGrant(
                frozenset({"SELECT", "INSERT"})
                if table in _WORKER_STAGE2_INSERT
                else frozenset({"SELECT"})
            )
        for table, columns in _WORKER_STAGE2_UPDATE_COLUMNS.items():
            current = grants.get(table, TableGrant(frozenset({"SELECT"})))
            grants[table] = TableGrant(current.privileges - {"UPDATE"}, columns)
    else:
        raise ValueError(f"unsupported runtime role:{role}")
    for table in _STAGE3_TABLES:
        grants[table] = TableGrant(frozenset({"SELECT"}))
    for table in _S11_EXECUTION_TABLES:
        grants[table] = TableGrant(frozenset({"SELECT"}))

    # Preserve the deliberately asymmetric formal-production and workflow
    # outbox boundaries established by s06_0019.  Group-level defaults above
    # are only a starting point; these high-risk tables require exact grants.
    if role == API_ROLE:
        grants["reporting.formal_report_production"] = TableGrant(
            frozenset({"SELECT", "INSERT", "UPDATE"})
        )
        grants["reporting.formal_report_output"] = TableGrant(frozenset({"SELECT"}))
        grants["integration.workflow_start_command"] = TableGrant(frozenset({"SELECT", "INSERT"}))
        grants["integration.workflow_signal_command"] = TableGrant(frozenset({"SELECT", "INSERT"}))
    else:
        grants["reporting.formal_report_production"] = TableGrant(frozenset({"SELECT", "UPDATE"}))
        grants["reporting.formal_report_output"] = TableGrant(frozenset({"SELECT", "INSERT"}))
        grants["integration.workflow_start_command"] = TableGrant(
            frozenset({"SELECT", "INSERT", "UPDATE"})
        )
        grants["integration.workflow_signal_command"] = TableGrant(frozenset({"SELECT", "UPDATE"}))

    # Append-only Service 2 history is written by both the API (manual review,
    # freeze and validation) and workers (automated analysis/lifecycle).  Keep
    # mutation privileges aligned with the append-only triggers.
    for table in (
        "platform.service2_analysis_attempt",
        "platform.service2_batch_event",
        "platform.service2_finding_review",
        "platform.service2_fact_manifest",
    ):
        grants[table] = TableGrant(frozenset({"SELECT", "INSERT"}))

    # Collection attempts/failure knowledge and paid-call state are worker
    # execution ledgers.  API routes may inspect them, but must not be able to
    # forge execution history or provider-call outcomes.
    for table in (
        "platform.collection_query_execution_attempt",
        "platform.collection_failure_knowledge",
    ):
        grants[table] = TableGrant(
            frozenset({"SELECT", "INSERT"})
            if role == WORKER_ROLE
            else frozenset({"SELECT"})
        )
    grants["platform.service2_model_call"] = TableGrant(
        frozenset({"SELECT", "INSERT", "UPDATE"})
        if role == WORKER_ROLE
        else frozenset({"SELECT"})
    )
    if role == WORKER_ROLE:
        grants["platform.collection_submission_operation"] = TableGrant(frozenset({"SELECT"}))
    return MappingProxyType(dict(sorted(grants.items())))


TABLE_GRANTS: Final = MappingProxyType({role: _build_table_policy(role) for role in RUNTIME_ROLES})

_SEQUENCES_BY_SCHEMA = {
    "analytics": _names(
        "analytics",
        "analysis_run_id_seq anomaly_event_id_seq answer_analysis_id_seq "
        "answer_brand_extract_id_seq answer_citation_relation_id_seq answer_id_seq "
        "citation_fact_id_seq metric_daily_id_seq metric_definition_id_seq metric_trace_id_seq",
    ),
    "evidence": _names(
        "evidence",
        "evidence_access_audit_id_seq evidence_access_grant_id_seq evidence_anchor_id_seq "
        "evidence_asset_id_seq evidence_diff_id_seq evidence_package_id_seq "
        "evidence_relation_id_seq evidence_snapshot_id_seq answer_share_artifact_id_seq "
        "answer_share_verification_event_id_seq",
    ),
    "reporting": _names(
        "reporting",
        "data_export_id_seq effect_retest_id_seq formal_report_output_id_seq "
        "formal_report_production_id_seq optimization_action_id_seq report_artifact_id_seq "
        "report_comment_id_seq report_component_id_seq report_delivery_id_seq "
        "report_event_id_seq report_evidence_reference_id_seq report_frozen_fact_id_seq "
        "report_id_seq report_review_id_seq report_version_id_seq",
    ),
    "intelligence": _names(
        "intelligence",
        "appeal_id_seq author_identity_id_seq claim_evidence_id_seq claim_id_seq "
        "claim_occurrence_id_seq content_item_id_seq content_version_id_seq "
        "detection_feature_id_seq detection_score_id_seq domain_profile_id_seq entity_id_seq "
        "evaluation_case_result_id_seq evaluation_dataset_case_id_seq graph_edge_id_seq "
        "human_verdict_id_seq investigation_id_seq propagation_event_id_seq "
        "similarity_edge_id_seq source_independence_id_seq",
    ),
    "integration": _names(
        "integration",
        "outbox_event_id_seq workflow_signal_command_id_seq workflow_start_command_id_seq",
    ),
    "sop": tuple(f"{table}_id_seq" for table in _SOP_TABLES),
    "posting": _names("posting", "attribution_id_seq batch_id_seq event_id_seq target_id_seq"),
    "notification": _names(
        "notification",
        "audit_event_id_seq delivery_command_id_seq interaction_id_seq notice_id_seq",
    ),
    "platform_api": _names(
        "platform",
        "collection_account_event_id_seq collection_browser_id_seq "
        "collection_phone_account_id_seq collection_platform_account_id_seq "
        "collection_region_id_seq login_attempt_id_seq",
    ),
    "platform_worker": _names(
        "platform",
        "collection_account_event_id_seq collection_browser_id_seq "
        "collection_phone_account_id_seq collection_platform_account_id_seq "
        "collection_region_id_seq disparagement_factcheck_id_seq site_audit_suggestion_id_seq",
    ),
}


def _sequence_policy(role: str) -> frozenset[str]:
    shared = {
        *_SEQUENCES_BY_SCHEMA["evidence"],
        *_SEQUENCES_BY_SCHEMA["intelligence"],
        *_SEQUENCES_BY_SCHEMA["notification"],
    }
    if role == API_ROLE:
        return frozenset(
            {
                *shared,
                *(
                    sequence
                    for sequence in _SEQUENCES_BY_SCHEMA["reporting"]
                    if not sequence.endswith("formal_report_output_id_seq")
                ),
                *_SEQUENCES_BY_SCHEMA["integration"],
                *_SEQUENCES_BY_SCHEMA["sop"],
                *_SEQUENCES_BY_SCHEMA["posting"],
                *_SEQUENCES_BY_SCHEMA["platform_api"],
            }
        )
    if role == WORKER_ROLE:
        return frozenset(
            {
                *shared,
                *(
                    sequence
                    for sequence in _SEQUENCES_BY_SCHEMA["reporting"]
                    if not sequence.endswith("formal_report_production_id_seq")
                ),
                *(
                    sequence
                    for sequence in _SEQUENCES_BY_SCHEMA["integration"]
                    if not sequence.endswith("workflow_signal_command_id_seq")
                ),
                *_SEQUENCES_BY_SCHEMA["analytics"],
                *_SEQUENCES_BY_SCHEMA["platform_worker"],
            }
        )
    raise ValueError(f"unsupported runtime role:{role}")


SEQUENCE_GRANTS: Final = MappingProxyType({role: _sequence_policy(role) for role in RUNTIME_ROLES})

RECONCILIATION_FUNCTION: Final = (
    "platform.record_collection_not_sent_proof_v2(uuid,uuid,uuid,text,text,text,text)"
)
WORKER_FUNCTIONS: Final = (
    RECONCILIATION_FUNCTION,
    "integration.business_alert_snapshot()",
    "platform.create_collection_submission_operation_v2("
    "uuid,uuid,text,integer,text,text,timestamptz,text,text,text,text,text,text,text,text)",
    "platform.prepare_collection_submission_request_v2("
    "uuid,uuid,uuid,integer,text,text,text,text,text,text,text,timestamptz)",
    "platform.claim_collection_submission_v2("
    "uuid,uuid,uuid,text,integer,uuid,integer,text,text,text,text,text,text,text,text,text,"
    "timestamptz)",
    "platform.mark_collection_dispatch_reconciliation_ready_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,timestamptz)",
    "platform.claim_collection_dispatch_reconciliation_v2(uuid,uuid,uuid,uuid,integer,text,text)",
    "platform.begin_collection_capture_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,timestamptz)",
    "platform.stage_collection_capture_manifest_v2("
    "uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,text,text,text,bigint,"
    "text,text,text,text,text,text,text,text,text,text,text,text,text,timestamptz,timestamptz,"
    "timestamptz)",
    "platform.finalize_collection_submission_v2("
    "uuid,uuid,uuid,uuid,uuid,integer,text,text,text,text,text,text,text,text,text,text,text,"
    "timestamptz,text,text,text,integer)",
    "platform.record_collection_slot_outcome_v2("
    "uuid,uuid,uuid,integer,integer,uuid,integer,integer,text,text,text,boolean,text,text,"
    "timestamptz)",
    "platform.link_collection_capture_v2(uuid,uuid,uuid,uuid,uuid,integer,text,text,timestamptz)",
    "platform.classify_collection_capture_orphan_v2(uuid,uuid,uuid,uuid,integer,timestamptz,text)",
    "platform.collection_capture_orphan_gc_eligible_v2(uuid,uuid,uuid,timestamptz)",
    "platform.advance_collection_governance_outbox_v2(uuid,uuid,uuid,integer,text,text)",
    "platform.create_collection_execution_plan_v2("
    "uuid,uuid,uuid,uuid,text,bigint,integer,text,timestamptz)",
    "platform.create_collection_execution_partition_v2("
    "uuid,uuid,uuid,uuid,text,bigint,bigint,bigint,text,text,text,text,bigint,integer,"
    "timestamptz)",
    "platform.finalize_collection_execution_plan_v2("
    "uuid,uuid,uuid,text,bigint,text,integer,timestamptz)",
    "platform.stage_collection_partition_workflow_start_v2("
    "uuid,uuid,uuid,uuid,text,text,bigint,bigint,integer,uuid,jsonb,timestamptz,timestamptz)",
    "platform.claim_collection_execution_start_outbox_v2("
    "uuid,uuid,uuid,text,text,integer,bigint,text,timestamptz)",
    "platform.finalize_collection_execution_start_outbox_v2("
    "uuid,uuid,uuid,text,text,bigint,integer,text,text,text,timestamptz)",
    "platform.read_collection_execution_control_v2(uuid,uuid,uuid,uuid,text)",
    "platform.advance_collection_execution_partition_v2("
    "uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,text,text,bigint,integer,timestamptz)",
    "platform.claim_collection_execution_reconciliation_v2("
    "uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,timestamptz)",
    "platform.cancel_collection_execution_partition_v2("
    "uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,timestamptz)",
    "platform.finalize_collection_execution_partition_v2("
    "uuid,uuid,uuid,uuid,text,bigint,bigint,integer,text,text,text,timestamptz)",
)
FUNCTION_GRANTS: Final = MappingProxyType(
    {API_ROLE: frozenset(), WORKER_ROLE: frozenset(WORKER_FUNCTIONS)}
)


def validate_policy() -> None:
    for role in RUNTIME_ROLES:
        for table, grant in TABLE_GRANTS[role].items():
            schema, separator, _name = table.partition(".")
            if not separator or schema not in MANAGED_SCHEMAS:
                raise RuntimeError(f"runtime_acl_invalid_table:{role}:{table}")
            if not grant.privileges <= set(TABLE_ACTIONS):
                raise RuntimeError(f"runtime_acl_invalid_action:{role}:{table}")
            if "UPDATE" in grant.privileges and grant.update_columns:
                raise RuntimeError(f"runtime_acl_duplicate_update_scope:{role}:{table}")
        if not all(sequence.count(".") == 1 for sequence in SEQUENCE_GRANTS[role]):
            raise RuntimeError(f"runtime_acl_invalid_sequence:{role}")


validate_policy()


def migration_reconcile_sql(role: str) -> str:
    """Render the head-migration reconciliation from the shared manifest."""

    if role not in RUNTIME_ROLES:
        raise ValueError(f"unsupported runtime role:{role}")
    statements = [
        f"DO $runtime_acl$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN"
    ]
    # Function EXECUTE is granted to PUBLIC by PostgreSQL's global default.
    # A schema-local default REVOKE cannot subtract a global default, so the
    # migration owner must be hardened at the owner-wide level first.
    statements.extend(
        (
            f'ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM "{role}";',
            "ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM PUBLIC;",
            f'ALTER DEFAULT PRIVILEGES REVOKE ALL ON SEQUENCES FROM "{role}";',
            "ALTER DEFAULT PRIVILEGES REVOKE ALL ON SEQUENCES FROM PUBLIC;",
            f'ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM "{role}";',
            "ALTER DEFAULT PRIVILEGES REVOKE ALL ON FUNCTIONS FROM PUBLIC;",
        )
    )
    for schema in MANAGED_SCHEMAS:
        statements.extend(
            (
                f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" FROM "{role}";',
                f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{schema}" FROM "{role}";',
                f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "{schema}" FROM "{role}";',
                f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" FROM PUBLIC;',
                f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{schema}" FROM PUBLIC;',
                f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "{schema}" FROM PUBLIC;',
                f'REVOKE ALL ON SCHEMA "{schema}" FROM "{role}";',
                f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC;',
                f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                f'REVOKE ALL ON TABLES FROM "{role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" REVOKE ALL ON TABLES FROM PUBLIC;',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                f'REVOKE ALL ON SEQUENCES FROM "{role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                "REVOKE ALL ON SEQUENCES FROM PUBLIC;",
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                f'REVOKE ALL ON FUNCTIONS FROM "{role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                "REVOKE ALL ON FUNCTIONS FROM PUBLIC;",
            )
        )
    for table, grant in TABLE_GRANTS[role].items():
        schema, name = table.split(".", 1)
        if grant.privileges:
            statements.append(
                f"GRANT {','.join(sorted(grant.privileges))} ON TABLE "
                f'"{schema}"."{name}" TO "{role}";'
            )
        if grant.update_columns:
            columns = ",".join(f'"{column}"' for column in grant.update_columns)
            statements.append(f'GRANT UPDATE ({columns}) ON TABLE "{schema}"."{name}" TO "{role}";')
    for sequence in sorted(SEQUENCE_GRANTS[role]):
        schema, name = sequence.split(".", 1)
        statements.append(f'GRANT USAGE,SELECT ON SEQUENCE "{schema}"."{name}" TO "{role}";')
    for function in sorted(FUNCTION_GRANTS[role]):
        statements.append(f'GRANT EXECUTE ON FUNCTION {function} TO "{role}";')
    statements.append("END IF; END $runtime_acl$;")
    return "\n".join(statements)


__all__ = [
    "API_ROLE",
    "FUNCTION_GRANTS",
    "MANAGED_SCHEMAS",
    "RECONCILIATION_FUNCTION",
    "RUNTIME_ROLES",
    "SEQUENCE_ACTIONS",
    "SEQUENCE_GRANTS",
    "TABLE_ACTIONS",
    "TABLE_GRANTS",
    "TableGrant",
    "WORKER_FUNCTIONS",
    "WORKER_ROLE",
    "validate_policy",
    "migration_reconcile_sql",
]
