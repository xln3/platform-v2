"""Real-PostgreSQL owner-loss recovery through the submission coordinator.

The lane is opt-in, loopback-only, and uses the dedicated S01 CI database via
``COLLECTION_SUBMISSION_V2_TEST_DSN``.  Fixture setup performs no provider,
browser, object-store, Temporal, or event-bus I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier
from typing import cast
from uuid import uuid4

import psycopg
import pytest
from geo_platform.collection.owner_reconciliation_v2 import (
    OwnerReconciliationError,
    PostgresAcceptedOwnerNotSentProofStore,
    build_owner_not_sent_proof_request,
    compose_postgres_owner_reconciliation_gateway,
    owner_reconciliation_evidence_digest,
    owner_reconciliation_evidence_ref,
)
from geo_platform.collection.resource_owner_gateway_v2 import (
    SubmissionOwnerSendJournalSnapshot,
)
from geo_platform.collection.submission_repository_v2 import (
    RepositoryConnection,
    RepositoryConnectionFactory,
    RepositoryScope,
)
from geo_platform.collection.submission_v2 import (
    ReconciliationEvidence,
    SubmissionCoordinator,
)

from domain.collection.submission import OutboxEventRef, operation_ref
from domain.collection.surface import SendState

from .test_collection_submission_repository_v2 import (
    _claim_once,
    _seed_submission_fixture,
    _set_scope,
    _submission_work,
    _SubmissionFixture,
    _test_dsn,
    _worker_repository,
)

pytestmark = pytest.mark.isolated_postgres


class _RealtimeClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _JournalReader:
    def __init__(self, snapshot: SubmissionOwnerSendJournalSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def load_send_journal(
        self,
        *,
        owner_dispatch_ref: str,
    ) -> SubmissionOwnerSendJournalSnapshot:
        self.calls += 1
        assert owner_dispatch_ref == self.snapshot.owner_dispatch_ref
        return self.snapshot


class _ForbiddenGateway:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"owner-loss recovery invoked forbidden gateway: {name}")


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[OutboxEventRef] = []

    def publish(self, event: OutboxEventRef) -> None:
        self.events.append(event)


def _role_connection_factory(
    dsn: str,
    *,
    role: str,
) -> RepositoryConnectionFactory:
    if role not in {"geo_api", "geo_worker"}:
        raise AssertionError("integration role is not allow-listed")

    def connect() -> RepositoryConnection:
        connection = psycopg.connect(dsn)
        connection.execute(f"SET ROLE {role}")
        connection.commit()
        return cast(RepositoryConnection, connection)

    return connect


def _scope(fixture: _SubmissionFixture) -> RepositoryScope:
    return RepositoryScope(
        tenant_id=fixture.tenant_id,
        project_id=fixture.project_id,
    )


def _terminate_owner_and_mark_ready(fixture: _SubmissionFixture) -> None:
    terminated_at = datetime.now(UTC)
    with psycopg.connect(fixture.dsn) as connection:
        with connection.transaction():
            released = connection.execute(
                """
                UPDATE platform.resource_lease
                   SET lease_state='released',released_at=%s,
                       reconciliation_reason='owner_runtime_integration',
                       version=version+1,updated_at=%s
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                   AND lease_state='active'
                """,
                (
                    terminated_at,
                    terminated_at,
                    fixture.operation_id,
                    fixture.tenant_id,
                    fixture.project_id,
                ),
            ).rowcount
            fenced = connection.execute(
                """
                UPDATE platform.collection_resource_capacity_unit AS capacity
                   SET capacity_state='available',
                       state_reason='owner_runtime_integration',
                       version=capacity.version+1,updated_at=%s
                  FROM platform.collection_execution_grant_resource AS resource
                 WHERE resource.execution_grant_id=%s
                   AND resource.tenant_id=%s AND resource.project_id=%s
                   AND capacity.id=resource.capacity_unit_id
                   AND capacity.tenant_id=resource.tenant_id
                   AND capacity.project_id=resource.project_id
                   AND capacity.capacity_state='leased'
                """,
                (
                    terminated_at,
                    fixture.execution_grant_id,
                    fixture.tenant_id,
                    fixture.project_id,
                ),
            ).rowcount
    assert released == len(fixture.leases)
    assert fenced == len(fixture.leases)

    with psycopg.connect(fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, fixture.tenant_id, role="geo_worker")
            dispatch = connection.execute(
                """
                SELECT id,reconciliation_version
                  FROM platform.collection_submission_dispatch_v2
                 WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
                """,
                (fixture.operation_id, fixture.tenant_id, fixture.project_id),
            ).fetchone()
            assert dispatch is not None
            marked = connection.execute(
                """
                SELECT platform.mark_collection_dispatch_reconciliation_ready_v2(
                  %s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP
                )
                """,
                (
                    fixture.tenant_id,
                    fixture.project_id,
                    fixture.operation_id,
                    dispatch[0],
                    dispatch[1],
                    "owner-runtime-loss-proof",
                    sha256(f"owner-runtime-loss:{fixture.operation_id}".encode()).hexdigest(),
                ),
            ).fetchone()
    assert marked == (2,)


def _dead_owner_fixture() -> _SubmissionFixture:
    fixture = _seed_submission_fixture(_test_dsn())
    assert _claim_once(fixture, Barrier(1))
    _terminate_owner_and_mark_ready(fixture)
    return fixture


def _journal_for(fixture: _SubmissionFixture) -> SubmissionOwnerSendJournalSnapshot:
    sending = _worker_repository(fixture).load_operation(fixture.operation_ref)
    assert sending is not None and sending.claim is not None
    return SubmissionOwnerSendJournalSnapshot(
        owner_dispatch_ref=sending.claim.owner_dispatch_ref,
        owner_authorization_evidence_sha256=sending.claim.owner_wal_evidence_sha256,
    )


def _proof_count(fixture: _SubmissionFixture) -> int:
    with psycopg.connect(fixture.dsn) as connection:
        row = connection.execute(
            """
            SELECT count(*)
              FROM platform.collection_submission_reconciliation_proof
             WHERE operation_id=%s AND tenant_id=%s AND project_id=%s
            """,
            (fixture.operation_id, fixture.tenant_id, fixture.project_id),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_real_coordinator_owner_wal_recovery_is_successful_and_idempotent() -> None:
    fixture = _dead_owner_fixture()
    repository = _worker_repository(fixture)
    journal = _JournalReader(_journal_for(fixture))
    clock = _RealtimeClock()
    publisher = _RecordingPublisher()
    binder = compose_postgres_owner_reconciliation_gateway(
        connection_factory=_role_connection_factory(fixture.dsn, role="geo_worker"),
        scope=_scope(fixture),
        journal_reader=journal,
        clock=clock,
    )
    forbidden = _ForbiddenGateway()
    coordinator = SubmissionCoordinator(
        repository=repository,
        preflight_gateway=forbidden,  # type: ignore[arg-type]
        submit_gateway=forbidden,  # type: ignore[arg-type]
        reconciliation_gateway=binder,
        capture_gateway=forbidden,  # type: ignore[arg-type]
        outbox_publisher=publisher,
        clock=clock,
    )

    first = coordinator.run(_submission_work(fixture))
    published_after_first = tuple(publisher.events)
    replay = coordinator.run(_submission_work(fixture))

    assert first.operation.send_state is SendState.CONFIRMED_NOT_SENT
    assert first.operation.terminal is not None
    assert first.operation.terminal.non_submission_proof_ref is not None
    assert first.operation.terminal.non_submission_proof_ref.startswith("crp_")
    assert first == replay
    assert journal.calls == 2
    assert _proof_count(fixture) == 1
    assert published_after_first
    assert tuple(publisher.events) == published_after_first


def test_existing_proof_payload_drift_fails_and_rolls_back_adapter_transaction() -> None:
    fixture = _dead_owner_fixture()
    repository = _worker_repository(fixture)
    work = _submission_work(fixture)
    sending = repository.load_operation(fixture.operation_ref)
    assert sending is not None and sending.send_state is SendState.SENDING
    repository_claim = repository.claim_reconciliation(work=work, operation=sending)
    worker_connections = _role_connection_factory(fixture.dsn, role="geo_worker")
    store = PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=worker_connections,
        scope=_scope(fixture),
    )
    admission = store.project_admission(
        repository_claim=repository_claim,
        sending_operation=sending,
    )
    journal = _journal_for(fixture)
    digest = owner_reconciliation_evidence_digest(admission=admission, journal=journal)
    request = build_owner_not_sent_proof_request(
        admission=admission,
        evidence=ReconciliationEvidence(
            durable_evidence_ref=owner_reconciliation_evidence_ref(digest),
            durable_evidence_sha256=digest,
            observed_at=datetime.now(UTC),
        ),
    )

    with psycopg.connect(fixture.dsn) as connection:
        with connection.transaction():
            _set_scope(connection, fixture.tenant_id, role="geo_worker")
            seeded = connection.execute(
                """
                SELECT platform.record_collection_not_sent_proof_v2(
                  %s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    fixture.tenant_id,
                    fixture.project_id,
                    fixture.operation_id,
                    request.owner_gateway_revision,
                    "drifted-owner-evidence",
                    request.evidence_sha256,
                    "owner_wal_reconciliation_proved_not_sent",
                ),
            ).fetchone()
    assert seeded is not None
    assert _proof_count(fixture) == 1

    with pytest.raises(OwnerReconciliationError, match="postgres_owner_not_sent_proof_row_drift"):
        store.accept_owner_not_sent(request)

    assert _proof_count(fixture) == 1
    persisted = repository.load_operation(fixture.operation_ref)
    assert persisted is not None and persisted.send_state is SendState.SENDING


def test_real_proof_store_rejects_api_role_and_cross_tenant_scope_without_writes() -> None:
    fixture = _dead_owner_fixture()
    repository = _worker_repository(fixture)
    work = _submission_work(fixture)
    sending = repository.load_operation(fixture.operation_ref)
    assert sending is not None
    repository_claim = repository.claim_reconciliation(work=work, operation=sending)
    worker_store = PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=_role_connection_factory(fixture.dsn, role="geo_worker"),
        scope=_scope(fixture),
    )
    admission = worker_store.project_admission(
        repository_claim=repository_claim,
        sending_operation=sending,
    )
    journal = _journal_for(fixture)
    digest = owner_reconciliation_evidence_digest(admission=admission, journal=journal)
    request = build_owner_not_sent_proof_request(
        admission=admission,
        evidence=ReconciliationEvidence(
            durable_evidence_ref=owner_reconciliation_evidence_ref(digest),
            durable_evidence_sha256=digest,
            observed_at=datetime.now(UTC),
        ),
    )

    api_store = PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=_role_connection_factory(fixture.dsn, role="geo_api"),
        scope=_scope(fixture),
    )
    with pytest.raises(OwnerReconciliationError, match="postgres_owner_not_sent_proof_failed"):
        api_store.accept_owner_not_sent(request)
    assert _proof_count(fixture) == 0

    other_tenant = uuid4()
    cross_tenant_store = PostgresAcceptedOwnerNotSentProofStore(
        connection_factory=_role_connection_factory(fixture.dsn, role="geo_worker"),
        scope=RepositoryScope(tenant_id=other_tenant, project_id=fixture.project_id),
    )
    cross_tenant_request = request.model_copy(update={"tenant_id": other_tenant})
    with pytest.raises(
        OwnerReconciliationError,
        match="postgres_owner_reconciliation_authority_missing_or_ambiguous",
    ):
        cross_tenant_store.accept_owner_not_sent(cross_tenant_request)
    assert _proof_count(fixture) == 0
    assert operation_ref(sending.identity) == request.operation
