from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from workflows.activities.collection import finalize_account_revocation


@dataclass
class SessionLifecycleInput:
    account_pub_id: str
    scope: str
    challenge_required: bool = False


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
    async def run(self, data: SessionLifecycleInput) -> str:
        if data.challenge_required:
            await workflow.wait_condition(
                lambda: self._verified or self._revoked, timeout=timedelta(days=7)
            )
        return "revoked" if self._revoked else f"active:{data.account_pub_id}:{data.scope}"


@dataclass
class RevocationInput:
    account_pub_id: str
    profile_versions: list[int]
    request_pub_id: str


@workflow.defn
class AccountRevocationWorkflow:
    @workflow.run
    async def run(self, data: RevocationInput) -> dict[str, object]:
        result = await workflow.execute_activity(
            finalize_account_revocation,
            data.request_pub_id,
            start_to_close_timeout=timedelta(minutes=2),
        )
        return {
            "account_pub_id": data.account_pub_id,
            "sessions_closed": True,
            "released_leases": result.released_leases,
            "purged_profile_versions": result.purged_profile_versions,
            "deletion_verified": result.deletion_verified,
        }


@workflow.defn
class HumanInterventionWorkflow:
    def __init__(self) -> None:
        self._result: str | None = None
        self._nonce: str | None = None

    @workflow.signal
    async def complete(self, nonce: str, platform_result: str) -> None:
        if nonce != self._nonce:
            self._nonce = nonce
            self._result = platform_result

    @workflow.run
    async def run(self, intervention_pub_id: str) -> str:
        del intervention_pub_id
        await workflow.wait_condition(lambda: self._result is not None, timeout=timedelta(days=7))
        assert self._result is not None
        return self._result
