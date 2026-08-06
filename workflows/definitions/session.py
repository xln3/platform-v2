from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from workflows.activities.collection import (
        finalize_account_revocation,
        prepare_collection_session,
        release_collection_session,
    )


@dataclass
class SessionLifecycleInput:
    tenant_pub_id: str
    account_pub_id: str
    scope: str
    holder: str
    challenge_required: bool = False


@dataclass(frozen=True)
class SessionLifecycleResult:
    state: str
    account_pub_id: str
    scope: str
    lease_pub_id: str
    fencing_token: int
    profile_version: int
    intervention_verified: bool
    lease_released: bool


@workflow.defn
class PlatformSessionLifecycleWorkflow:
    def __init__(self) -> None:
        self._verified = False
        self._nonce: str | None = None
        self._revoked = False

    @workflow.signal
    async def intervention_completed(self, nonce: str) -> None:
        if nonce != self._nonce:
            self._nonce = nonce
            self._verified = True

    @workflow.signal
    async def revoke(self) -> None:
        self._revoked = True

    @workflow.run
    async def run(self, data: SessionLifecycleInput) -> SessionLifecycleResult:
        # Admission and lease acquisition are one database transaction in the
        # Activity. This prevents a workflow from representing an "active"
        # session based only on caller-supplied account and scope strings.
        preparation = await workflow.execute_activity(
            prepare_collection_session,
            args=[data.tenant_pub_id, data.account_pub_id, data.holder, data.scope],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                non_retryable_error_types=[
                    "account_not_active",
                    "scope_not_authorized",
                    "active_profile_not_found",
                ],
            ),
        )
        try:
            if data.challenge_required:
                await workflow.wait_condition(
                    lambda: self._verified or self._revoked, timeout=timedelta(days=7)
                )
            return SessionLifecycleResult(
                state="revoked" if self._revoked else "completed",
                account_pub_id=data.account_pub_id,
                scope=data.scope,
                lease_pub_id=preparation.lease_pub_id,
                fencing_token=preparation.fencing_token,
                profile_version=preparation.profile_version,
                intervention_verified=self._verified,
                lease_released=True,
            )
        finally:
            # External cancellation, challenge timeout, duplicate/revocation
            # Signals and normal completion all converge on the same fenced,
            # idempotent release path.
            await workflow.execute_activity(
                release_collection_session,
                args=[
                    data.tenant_pub_id,
                    preparation.lease_pub_id,
                    preparation.fencing_token,
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=10),
            )


@dataclass
class RevocationInput:
    tenant_pub_id: str
    account_pub_id: str
    profile_versions: list[int]


@dataclass(frozen=True)
class AccountRevocationResult:
    account_pub_id: str
    sessions_closed: bool
    released_leases: int
    purged_profile_versions: list[int]
    revoked_device_bindings: int
    revoked_terminal_tasks: int
    revoked_interventions: int
    revoked_capability_leases: int
    deletion_verified: bool


@workflow.defn
class AccountRevocationWorkflow:
    @workflow.run
    async def run(self, data: RevocationInput) -> AccountRevocationResult:
        result = await workflow.execute_activity(
            finalize_account_revocation,
            args=[data.tenant_pub_id, data.account_pub_id],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(minutes=1),
            ),
        )
        return AccountRevocationResult(
            account_pub_id=data.account_pub_id,
            sessions_closed=True,
            released_leases=result.released_leases,
            purged_profile_versions=result.purged_profile_versions,
            revoked_device_bindings=result.revoked_device_bindings,
            revoked_terminal_tasks=result.revoked_terminal_tasks,
            revoked_interventions=result.revoked_interventions,
            revoked_capability_leases=result.revoked_capability_leases,
            deletion_verified=result.deletion_verified,
        )


@workflow.defn
class HumanInterventionWorkflow:
    def __init__(self) -> None:
        self._result: str | None = None
        self._nonce: str | None = None

    @workflow.signal
    async def complete(self, nonce: str, platform_result: str) -> None:
        # A terminal intervention is immutable once the first accepted platform
        # result arrives. Retries with either the same or a different nonce
        # cannot overwrite the authoritative outcome.
        if self._result is not None:
            return
        if platform_result not in {"verified", "failed", "expired", "rejected"}:
            return
        self._nonce = nonce
        self._result = platform_result

    @workflow.run
    async def run(self, intervention_pub_id: str) -> str:
        del intervention_pub_id
        await workflow.wait_condition(lambda: self._result is not None, timeout=timedelta(days=7))
        assert self._result is not None
        return self._result
