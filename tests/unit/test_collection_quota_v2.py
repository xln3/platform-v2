from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal
from uuid import UUID

import pytest
from geo_platform.collection.quota_v2 import (
    ADVISORY_BUCKET_LOCK_SQL,
    INCREMENT_BUCKET_RESERVED_SQL,
    INSERT_BUCKET_SQL,
    INSERT_LEDGER_EVENT_SQL,
    INSERT_RESERVATION_EFFECT_SQL,
    INSERT_RESERVATION_SQL,
    LOAD_ADMITTED_OPERATION_AND_BINDING_SQL,
    LOAD_AUTHORITATIVE_SCOPES_SQL,
    LOAD_OPERATION_AND_BINDING_SQL,
    LOAD_OPERATION_SQL,
    LOAD_RESERVATION_EFFECTS_SQL,
    LOAD_RESERVATION_SQL,
    LOCK_BUCKET_BY_ID_SQL,
    LOCK_BUCKET_SQL,
    MARK_RESERVATION_RESERVED_SQL,
    MARK_RESERVATION_TERMINAL_SQL,
    RECORD_NOT_SENT_PROOF_SQL,
    SET_TENANT_SQL,
    SETTLE_BUCKET_SQL,
    UPDATE_EFFECT_TERMINAL_SQL,
    UPDATE_OPERATION_SEND_STATE_SQL,
    ExplicitProviderWindow,
    LedgerEffectKind,
    OwnerEvidence,
    QuotaBlocker,
    QuotaV2Error,
    ReconcileQuotaRequest,
    ReconciliationAction,
    ReservationDisposition,
    ReserveQuotaRequest,
    SettleQuotaRequest,
    materialize_quota_buckets,
    reconcile_quota,
    reconciliation_action,
    reservation_set_hash,
    reserve_quota,
    reserve_quota_after_operation_admission,
    settle_quota,
    settlement_effect,
)

from domain.collection.surface import (
    CollectionSurface,
    QuotaScopeDeclaration,
    QuotaScopeKind,
    QuotaWindowPolicy,
    QuotaWindowUnit,
    SendState,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("20000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("30000000-0000-0000-0000-000000000001")
BINDING_ID = UUID("40000000-0000-0000-0000-000000000001")
REGISTRY_ID = UUID("50000000-0000-0000-0000-000000000001")
POLICY_ID = UUID("60000000-0000-0000-0000-000000000001")
BUCKET_ID = UUID("70000000-0000-0000-0000-000000000001")
RESERVATION_ID = UUID("80000000-0000-0000-0000-000000000001")
EFFECT_ID = UUID("90000000-0000-0000-0000-000000000001")


class _Cursor:
    def __init__(self, rows: Sequence[Sequence[object]]) -> None:
        self.rows = tuple(rows)

    def fetchone(self) -> Sequence[object] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self.rows


Handler = Callable[[Mapping[str, object]], Sequence[Sequence[object]]]


class _Transaction(AbstractContextManager[None]):
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> None:
        self.connection.transactions += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is None:
            self.connection.commits += 1
        else:
            self.connection.rollbacks += 1
        return False


class _FakeConnection:
    def __init__(self, handlers: Mapping[str, Handler]) -> None:
        self.handlers = dict(handlers)
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0

    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> _Cursor:
        safe_params = dict(params or {})
        self.calls.append((query, safe_params))
        handler = self.handlers.get(query)
        if handler is None:
            raise AssertionError(f"unexpected SQL: {query.strip()[:80]}")
        return _Cursor(handler(safe_params))

    def transaction(self) -> AbstractContextManager[None]:
        return _Transaction(self)


def _none(_params: Mapping[str, object]) -> Sequence[Sequence[object]]:
    return ()


def _return_param(name: str) -> Handler:
    def handler(params: Mapping[str, object]) -> Sequence[Sequence[object]]:
        return ((params[name],),)

    return handler


def _scope(
    kind: QuotaScopeKind,
    *,
    limit: int = 10,
    unit: QuotaWindowUnit = QuotaWindowUnit.DAY,
    size: int = 1,
    timezone: str = "Asia/Shanghai",
) -> QuotaScopeDeclaration:
    platform: str | None = None
    collection_surface: CollectionSurface | None = None
    product_variant: str | None = None
    interaction_mode: str | None = None
    if kind is QuotaScopeKind.PLATFORM_SURFACE:
        platform = "doubao"
        collection_surface = CollectionSurface.CONSUMER_WEB
        product_variant = "chat"
    elif kind is QuotaScopeKind.MODE:
        interaction_mode = "search"
    return QuotaScopeDeclaration(
        policy_revision="quota-policy-v1",
        scope_kind=kind,
        scope_subject_id=f"subject-{kind.value}",
        limit=limit,
        window=QuotaWindowPolicy(
            unit=unit,
            size=size,
            timezone=timezone,
            boundary_revision="calendar-v1",
            provider_window_code=(
                "provider-cycle" if unit is QuotaWindowUnit.PROVIDER_CUSTOM else None
            ),
        ),
        platform=platform,
        collection_surface=collection_surface,
        product_variant=product_variant,
        interaction_mode=interaction_mode,
    )


def _operation_binding_row(*, send_state: SendState = SendState.NOT_SENT) -> Sequence[object]:
    return (
        send_state.value,
        1,
        "operation-key-v1",
        datetime(2026, 8, 24, 4, tzinfo=UTC),
        datetime(2026, 8, 24, 5, tzinfo=UTC),
        "doubao",
        CollectionSurface.CONSUMER_WEB.value,
        "chat",
        "search",
        "registry-v1",
        "quota-policy-v1",
    )


def _operation_row(*, send_state: SendState) -> Sequence[object]:
    return _operation_binding_row(send_state=send_state)[:9]


def _authoritative_scope_row(
    scope: QuotaScopeDeclaration,
    *,
    policy_id: UUID = POLICY_ID,
    quota_units: int = 1,
    ordinal: int = 0,
) -> Sequence[object]:
    return (
        "registry-v1",
        "quota-scope-lock-order-v1",
        policy_id,
        scope.schema_version,
        scope.scope_key,
        scope.selector_key,
        scope.policy_revision,
        scope.scope_kind.value,
        scope.scope_subject_id,
        scope.platform,
        scope.collection_surface.value if scope.collection_surface else None,
        scope.product_variant,
        scope.interaction_mode,
        scope.window.schema_version,
        scope.window.unit.value,
        scope.window.size,
        scope.window.timezone,
        scope.window.boundary_revision,
        scope.window.provider_window_code,
        scope.limit,
        scope.lock_order_key[0],
        quota_units,
        ordinal,
        scope.scope_key,
        scope.scope_kind.value,
        scope.scope_subject_id,
        scope.policy_revision,
    )


def _bucket_row(
    bucket_hash: object,
    *,
    bucket_id: UUID = BUCKET_ID,
    policy_id: UUID = POLICY_ID,
    limit: int = 1,
    reserved: int = 0,
    consumed: int = 0,
    unknown: int = 0,
) -> Sequence[object]:
    return (
        bucket_id,
        policy_id,
        bucket_hash,
        datetime(2026, 8, 23, 16, tzinfo=UTC),
        datetime(2026, 8, 24, 16, tzinfo=UTC),
        limit,
        reserved,
        consumed,
        unknown,
        "open",
    )


def test_materialize_uses_frozen_scope_order_not_caller_order() -> None:
    scopes = (
        _scope(QuotaScopeKind.MODE),
        _scope(QuotaScopeKind.PROJECT),
        _scope(QuotaScopeKind.ACCOUNT),
        _scope(QuotaScopeKind.PROVIDER),
    )

    buckets = materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=scopes,
        occurred_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    assert tuple(bucket.scope.scope_kind for bucket in buckets) == (
        QuotaScopeKind.PROVIDER,
        QuotaScopeKind.ACCOUNT,
        QuotaScopeKind.PROJECT,
        QuotaScopeKind.MODE,
    )
    assert all(str(TENANT_ID) in bucket.bucket_key for bucket in buckets)
    assert all(str(PROJECT_ID) in bucket.bucket_key for bucket in buckets)
    assert len({bucket.bucket_hash for bucket in buckets}) == len(buckets)


def test_calendar_window_honours_timezone_dst_and_multi_week_anchor() -> None:
    day = _scope(QuotaScopeKind.ACCOUNT, timezone="America/New_York")
    fortnight = _scope(
        QuotaScopeKind.PROJECT,
        unit=QuotaWindowUnit.WEEK,
        size=2,
        timezone="Asia/Shanghai",
    )

    day_bucket, fortnight_bucket = materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=(fortnight, day),
        occurred_at=datetime(2026, 3, 8, 16, tzinfo=UTC),
    )

    assert day_bucket.starts_at == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert day_bucket.ends_at == datetime(2026, 3, 9, 4, tzinfo=UTC)
    assert fortnight_bucket.starts_at.weekday() == 6  # UTC Sunday == Shanghai Monday.
    assert fortnight_bucket.ends_at - fortnight_bucket.starts_at == pytest.approx(
        datetime(2026, 3, 16, 16, tzinfo=UTC) - datetime(2026, 3, 2, 16, tzinfo=UTC)
    )


def test_provider_window_must_exactly_cover_custom_scopes() -> None:
    custom = _scope(QuotaScopeKind.PROVIDER, unit=QuotaWindowUnit.PROVIDER_CUSTOM)
    with pytest.raises(QuotaV2Error, match="provider_window_set_mismatch"):
        materialize_quota_buckets(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            scopes=(custom,),
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        )

    explicit = ExplicitProviderWindow(
        scope_key=custom.scope_key,
        starts_at=datetime(2026, 8, 20, tzinfo=UTC),
        ends_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    (bucket,) = materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=(custom,),
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        provider_windows=(explicit,),
    )
    assert bucket.starts_at == explicit.starts_at
    assert bucket.ends_at == explicit.ends_at


def test_provider_window_rejects_duplicate_or_non_covering_policy_results() -> None:
    custom = _scope(QuotaScopeKind.PROVIDER, unit=QuotaWindowUnit.PROVIDER_CUSTOM)
    covering = ExplicitProviderWindow(
        scope_key=custom.scope_key,
        starts_at=datetime(2026, 8, 20, tzinfo=UTC),
        ends_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    with pytest.raises(QuotaV2Error, match="provider_window_set_duplicate"):
        materialize_quota_buckets(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            scopes=(custom,),
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            provider_windows=(covering, covering),
        )

    stale = ExplicitProviderWindow(
        scope_key=custom.scope_key,
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with pytest.raises(
        QuotaV2Error,
        match="provider_window_does_not_cover_occurrence",
    ):
        materialize_quota_buckets(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            scopes=(custom,),
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            provider_windows=(stale,),
        )


def test_unknown_calendar_boundary_revision_fails_closed() -> None:
    scope = QuotaScopeDeclaration(
        policy_revision="policy-v2",
        scope_kind=QuotaScopeKind.PROJECT,
        scope_subject_id="project-subject",
        limit=10,
        window=QuotaWindowPolicy(
            unit=QuotaWindowUnit.DAY,
            timezone="UTC",
            boundary_revision="unimplemented-boundary-v2",
        ),
    )
    with pytest.raises(QuotaV2Error, match="unsupported_quota_boundary_revision"):
        materialize_quota_buckets(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            scopes=(scope,),
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        )


def test_reservation_set_hash_is_order_independent_after_materialization() -> None:
    scopes = (_scope(QuotaScopeKind.MODE), _scope(QuotaScopeKind.PROVIDER))
    forward = materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=scopes,
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    reverse = materialize_quota_buckets(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        scopes=tuple(reversed(scopes)),
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert reservation_set_hash(forward, requested_units=1) == reservation_set_hash(
        reverse, requested_units=1
    )


def _successful_reserve_fake(
    scopes: tuple[QuotaScopeDeclaration, ...],
    *,
    reserved_units: int | Mapping[QuotaScopeKind, int] = 0,
    operation_sql: str = LOAD_OPERATION_AND_BINDING_SQL,
) -> tuple[_FakeConnection, dict[str, object]]:
    captured: dict[str, object] = {}
    bucket_params: dict[str, Mapping[str, object]] = {}
    bucket_ids: dict[str, UUID] = {}

    def insert_bucket(params: Mapping[str, object]) -> Sequence[Sequence[object]]:
        bucket_hash = str(params["bucket_hash"])
        bucket_params[bucket_hash] = params
        bucket_ids[bucket_hash] = UUID(int=len(bucket_ids) + 100)
        return ()

    def lock_bucket(params: Mapping[str, object]) -> Sequence[Sequence[object]]:
        bucket_hash = str(params["bucket_hash"])
        inserted = bucket_params[bucket_hash]
        scope_kind = QuotaScopeKind(str(inserted["scope_kind"]))
        bucket_reserved = (
            reserved_units.get(scope_kind, 0)
            if isinstance(reserved_units, Mapping)
            else reserved_units
        )
        return (
            (
                bucket_ids[bucket_hash],
                inserted["scope_policy_id"],
                bucket_hash,
                inserted["window_start"],
                inserted["window_end"],
                inserted["limit_units"],
                bucket_reserved,
                0,
                0,
                "open",
            ),
        )

    def insert_reservation(params: Mapping[str, object]) -> Sequence[Sequence[object]]:
        captured.update(params)
        return ((params["id"],),)

    policy_ids = tuple(UUID(int=index + 10) for index in range(len(scopes)))
    handlers: dict[str, Handler] = {
        SET_TENANT_SQL: _none,
        operation_sql: lambda _params: ((_operation_binding_row()),),
        LOAD_AUTHORITATIVE_SCOPES_SQL: lambda _params: tuple(
            _authoritative_scope_row(scope, policy_id=policy_ids[index], ordinal=index)
            for index, scope in enumerate(scopes)
        ),
        LOAD_RESERVATION_SQL: _none,
        ADVISORY_BUCKET_LOCK_SQL: _none,
        INSERT_BUCKET_SQL: insert_bucket,
        LOCK_BUCKET_SQL: lock_bucket,
        INSERT_RESERVATION_SQL: insert_reservation,
        INSERT_RESERVATION_EFFECT_SQL: _return_param("id"),
        INCREMENT_BUCKET_RESERVED_SQL: _return_param("bucket_id"),
        INSERT_LEDGER_EVENT_SQL: _return_param("id"),
        MARK_RESERVATION_RESERVED_SQL: _return_param("reservation_id"),
    }
    return (_FakeConnection(handlers), captured)


def _reserve_request() -> ReserveQuotaRequest:
    return ReserveQuotaRequest(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        operation_id=OPERATION_ID,
        binding_id=BINDING_ID,
        registry_id=REGISTRY_ID,
        requested_units=1,
    )


def test_production_reserve_loads_authoritative_scopes_and_locks_canonically() -> None:
    scopes = (
        _scope(QuotaScopeKind.PROVIDER, limit=3),
        _scope(QuotaScopeKind.MODE, limit=3),
    )
    connection, _captured = _successful_reserve_fake(scopes)

    result = reserve_quota(connection, _reserve_request())

    assert result.reserved is True
    assert result.idempotent is False
    assert result.reservation_id is not None
    assert connection.commits == 1
    assert connection.rollbacks == 0
    queries = [query for query, _params in connection.calls]
    assert queries[:3] == [
        SET_TENANT_SQL,
        LOAD_OPERATION_AND_BINDING_SQL,
        LOAD_AUTHORITATIVE_SCOPES_SQL,
    ]
    assert "FOR UPDATE OF op, binding" not in LOAD_OPERATION_AND_BINDING_SQL
    assert "FOR UPDATE OF op" in LOAD_OPERATION_AND_BINDING_SQL
    assert "FOR SHARE OF binding" not in LOAD_OPERATION_AND_BINDING_SQL
    assert "FOR SHARE" not in LOAD_AUTHORITATIVE_SCOPES_SQL
    advisory_keys = [
        str(params["bucket_key"])
        for query, params in connection.calls
        if query == ADVISORY_BUCKET_LOCK_SQL
    ]
    assert [
        next(kind.value for kind in QuotaScopeKind if f"scope_kind={kind.value}|" in key)
        for key in advisory_keys
    ] == [QuotaScopeKind.PROVIDER.value, QuotaScopeKind.MODE.value]
    first_insert = queries.index(INSERT_BUCKET_SQL)
    last_advisory = len(queries) - 1 - queries[::-1].index(ADVISORY_BUCKET_LOCK_SQL)
    first_lock = queries.index(LOCK_BUCKET_SQL)
    last_insert = len(queries) - 1 - queries[::-1].index(INSERT_BUCKET_SQL)
    assert last_advisory < first_insert <= last_insert < first_lock
    assert queries.count(INSERT_LEDGER_EVENT_SQL) == len(scopes)


def test_stage3_reserve_reuses_the_restricted_admission_row_lock() -> None:
    connection, _captured = _successful_reserve_fake(
        (_scope(QuotaScopeKind.PROVIDER, limit=3),),
        operation_sql=LOAD_ADMITTED_OPERATION_AND_BINDING_SQL,
    )

    result = reserve_quota_after_operation_admission(connection, _reserve_request())

    assert result.reserved
    assert "FOR UPDATE OF op" not in LOAD_ADMITTED_OPERATION_AND_BINDING_SQL
    assert connection.calls[1][0] == LOAD_ADMITTED_OPERATION_AND_BINDING_SQL


def test_any_insufficient_bucket_rolls_back_without_reservation_or_ledger() -> None:
    connection, _captured = _successful_reserve_fake(
        (_scope(QuotaScopeKind.PROVIDER, limit=1),),
        reserved_units=1,
    )

    result = reserve_quota(connection, _reserve_request())

    assert result.reserved is False
    assert len(result.blockers) == 1
    assert result.blockers[0].available_units == 0
    assert connection.commits == 0
    assert connection.rollbacks == 1
    queries = [query for query, _params in connection.calls]
    assert INSERT_RESERVATION_SQL not in queries
    assert INSERT_RESERVATION_EFFECT_SQL not in queries
    assert INSERT_LEDGER_EVENT_SQL not in queries


def test_day_week_year_mode_surface_registry_reserves_only_as_one_complete_set() -> None:
    scopes = (
        _scope(QuotaScopeKind.MODE, limit=1),
        _scope(QuotaScopeKind.PLATFORM_SURFACE, limit=5),
        _scope(QuotaScopeKind.PROJECT, limit=5, unit=QuotaWindowUnit.YEAR),
        _scope(QuotaScopeKind.CREDENTIAL, limit=5),
        _scope(QuotaScopeKind.PROJECT, limit=5, unit=QuotaWindowUnit.WEEK),
        _scope(QuotaScopeKind.CONTRACT, limit=5),
        _scope(QuotaScopeKind.ACCOUNT, limit=5),
        _scope(QuotaScopeKind.PROJECT, limit=5),
        _scope(QuotaScopeKind.PROVIDER, limit=5),
    )
    connection, _captured = _successful_reserve_fake(
        scopes,
        reserved_units={QuotaScopeKind.MODE: 1},
    )

    result = reserve_quota(connection, _reserve_request())

    assert result.reserved is False
    assert len(result.blockers) == 1
    assert result.blockers[0].scope_kind is QuotaScopeKind.MODE
    assert connection.rollbacks == 1
    queries = [query for query, _params in connection.calls]
    assert queries.count(LOCK_BUCKET_SQL) == len(scopes)
    assert INCREMENT_BUCKET_RESERVED_SQL not in queries
    assert INSERT_RESERVATION_SQL not in queries
    assert INSERT_RESERVATION_EFFECT_SQL not in queries
    assert INSERT_LEDGER_EVENT_SQL not in queries


def test_repeated_operation_returns_exact_existing_reservation_without_new_effect() -> None:
    scope = _scope(QuotaScopeKind.PROVIDER, limit=3)
    first, captured = _successful_reserve_fake((scope,))
    first_result = reserve_quota(first, _reserve_request())
    bucket_params = next(params for query, params in first.calls if query == INSERT_BUCKET_SQL)
    assert first_result.reservation_id is not None
    second = _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_AND_BINDING_SQL: lambda _params: ((_operation_binding_row()),),
            LOAD_AUTHORITATIVE_SCOPES_SQL: lambda _params: (
                (_authoritative_scope_row(scope, policy_id=UUID(int=10))),
            ),
            LOAD_RESERVATION_SQL: lambda _params: (
                (
                    first_result.reservation_id,
                    "reserved",
                    1,
                    1,
                    captured["effect_set_hash"],
                    BINDING_ID,
                    REGISTRY_ID,
                ),
            ),
            LOAD_RESERVATION_EFFECTS_SQL: lambda _params: (
                (
                    EFFECT_ID,
                    BUCKET_ID,
                    UUID(int=10),
                    1,
                    "reserved",
                    bucket_params["bucket_hash"],
                    bucket_params["bucket_key"],
                    QuotaScopeKind.PROVIDER.value,
                ),
            ),
        }
    )

    result = reserve_quota(second, _reserve_request())

    assert result.reserved is True
    assert result.idempotent is True
    assert result.reservation_id == first_result.reservation_id
    queries = [query for query, _params in second.calls]
    assert ADVISORY_BUCKET_LOCK_SQL not in queries
    assert INSERT_RESERVATION_EFFECT_SQL not in queries
    assert INSERT_LEDGER_EVENT_SQL not in queries


def test_reserve_request_has_no_caller_scope_limit_or_provider_window_fields() -> None:
    assert set(ReserveQuotaRequest.__dataclass_fields__) == {
        "tenant_id",
        "project_id",
        "operation_id",
        "binding_id",
        "registry_id",
        "requested_units",
    }
    normalized = " ".join(LOAD_AUTHORITATIVE_SCOPES_SQL.split()).lower()
    assert "collection_binding_quota_scope" in normalized
    assert "collection_quota_scope_policy" in normalized
    assert "collection_quota_registry_revision" in normalized
    assert "binding.id = %(binding_id)s" in normalized
    assert "binding.lifecycle_state = 'active'" in normalized


@pytest.mark.parametrize(
    ("tenant_id", "project_id"),
    [
        (UUID("10000000-0000-0000-0000-000000000099"), PROJECT_ID),
        (TENANT_ID, UUID("20000000-0000-0000-0000-000000000099")),
    ],
)
def test_reserve_fails_closed_for_cross_scope_operation_ids(
    tenant_id: UUID,
    project_id: UUID,
) -> None:
    def scoped_operation(params: Mapping[str, object]) -> Sequence[Sequence[object]]:
        if params["tenant_id"] == TENANT_ID and params["project_id"] == PROJECT_ID:
            return ((_operation_binding_row()),)
        return ()

    connection = _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_AND_BINDING_SQL: scoped_operation,
        }
    )

    with pytest.raises(QuotaV2Error, match="operation_binding_scope_mismatch"):
        reserve_quota(
            connection,
            ReserveQuotaRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                operation_id=OPERATION_ID,
                binding_id=BINDING_ID,
                registry_id=REGISTRY_ID,
                requested_units=1,
            ),
        )

    assert connection.rollbacks == 1
    assert LOAD_AUTHORITATIVE_SCOPES_SQL not in [query for query, _params in connection.calls]


def test_live_provider_custom_scope_fails_closed_without_trusted_resolver() -> None:
    custom = _scope(QuotaScopeKind.PROVIDER, unit=QuotaWindowUnit.PROVIDER_CUSTOM)
    connection, _captured = _successful_reserve_fake((custom,))

    with pytest.raises(QuotaV2Error, match="provider_window_resolution_unavailable"):
        reserve_quota(connection, _reserve_request())

    assert connection.rollbacks == 1
    assert INSERT_BUCKET_SQL not in [query for query, _params in connection.calls]


def _settlement_fake(
    *,
    current_send_state: SendState,
    reservation_state: str = "reserved",
    effect_state: str = "reserved",
) -> _FakeConnection:
    effect_row = (
        EFFECT_ID,
        BUCKET_ID,
        POLICY_ID,
        1,
        effect_state,
        "b" * 64,
        "bucket-key",
        QuotaScopeKind.PROVIDER.value,
    )
    return _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_SQL: lambda _params: ((_operation_row(send_state=current_send_state)),),
            LOAD_RESERVATION_SQL: lambda _params: (
                (
                    RESERVATION_ID,
                    reservation_state,
                    1,
                    1,
                    "a" * 64,
                    BINDING_ID,
                    REGISTRY_ID,
                ),
            ),
            LOAD_RESERVATION_EFFECTS_SQL: lambda _params: (effect_row,),
            UPDATE_OPERATION_SEND_STATE_SQL: _return_param("operation_id"),
            LOCK_BUCKET_BY_ID_SQL: lambda _params: ((_bucket_row("b" * 64, reserved=1)),),
            UPDATE_EFFECT_TERMINAL_SQL: _return_param("effect_id"),
            SETTLE_BUCKET_SQL: _return_param("bucket_id"),
            INSERT_LEDGER_EVENT_SQL: _return_param("id"),
            MARK_RESERVATION_TERMINAL_SQL: _return_param("reservation_id"),
        }
    )


@pytest.mark.parametrize(
    ("current", "target", "disposition", "consumed_delta", "unknown_delta"),
    [
        (
            SendState.SENDING,
            SendState.CONFIRMED_SENT,
            ReservationDisposition.CONSUMED,
            1,
            0,
        ),
        (
            SendState.SENDING,
            SendState.SEND_UNKNOWN,
            ReservationDisposition.UNKNOWN_CONSUMED,
            0,
            1,
        ),
        (
            SendState.NOT_SENT,
            SendState.CONFIRMED_NOT_SENT,
            ReservationDisposition.RELEASED,
            0,
            0,
        ),
    ],
)
def test_settlement_updates_send_truth_before_conserving_every_bucket(
    current: SendState,
    target: SendState,
    disposition: ReservationDisposition,
    consumed_delta: int,
    unknown_delta: int,
) -> None:
    connection = _settlement_fake(current_send_state=current)

    result = settle_quota(
        connection,
        SettleQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            target_send_state=target,
            reason_code="owner_evidence_v1",
        ),
    )

    assert result.disposition is disposition
    assert result.idempotent is False
    queries = [query for query, _params in connection.calls]
    assert queries.index(UPDATE_OPERATION_SEND_STATE_SQL) < queries.index(
        UPDATE_EFFECT_TERMINAL_SQL
    )
    settle_params = next(params for query, params in connection.calls if query == SETTLE_BUCKET_SQL)
    assert settle_params["consumed_delta"] == consumed_delta
    assert settle_params["unknown_delta"] == unknown_delta
    ledger_params = next(
        params for query, params in connection.calls if query == INSERT_LEDGER_EVENT_SQL
    )
    assert ledger_params["from_state"] == "reserved"
    assert ledger_params["to_state"] == disposition.value
    assert connection.commits == 1


def test_repeated_terminal_settlement_is_idempotent_without_new_ledger_event() -> None:
    connection = _settlement_fake(
        current_send_state=SendState.CONFIRMED_SENT,
        reservation_state=ReservationDisposition.CONSUMED.value,
        effect_state=ReservationDisposition.CONSUMED.value,
    )

    result = settle_quota(
        connection,
        SettleQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            target_send_state=SendState.CONFIRMED_SENT,
            reason_code="idempotent_retry_v1",
        ),
    )

    assert result.idempotent is True
    queries = [query for query, _params in connection.calls]
    assert INSERT_LEDGER_EVENT_SQL not in queries
    assert UPDATE_EFFECT_TERMINAL_SQL not in queries
    assert SETTLE_BUCKET_SQL not in queries


@pytest.mark.parametrize(
    ("send_state", "disposition"),
    [
        (SendState.CONFIRMED_NOT_SENT, ReservationDisposition.RELEASED),
        (SendState.SEND_UNKNOWN, ReservationDisposition.UNKNOWN_CONSUMED),
    ],
)
def test_repeated_release_and_unknown_settlement_are_idempotent(
    send_state: SendState,
    disposition: ReservationDisposition,
) -> None:
    connection = _settlement_fake(
        current_send_state=send_state,
        reservation_state=disposition.value,
        effect_state=disposition.value,
    )

    result = settle_quota(
        connection,
        SettleQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            target_send_state=send_state,
            reason_code="idempotent_terminal_retry_v1",
        ),
    )

    assert result.disposition is disposition
    assert result.idempotent is True
    queries = [query for query, _params in connection.calls]
    assert UPDATE_OPERATION_SEND_STATE_SQL not in queries
    assert INSERT_LEDGER_EVENT_SQL not in queries
    assert UPDATE_EFFECT_TERMINAL_SQL not in queries
    assert SETTLE_BUCKET_SQL not in queries


def test_direct_settlement_cannot_release_a_sending_operation() -> None:
    connection = _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_SQL: lambda _params: ((_operation_row(send_state=SendState.SENDING)),),
        }
    )

    with pytest.raises(
        QuotaV2Error,
        match="sending_quota_release_requires_reconciliation",
    ):
        settle_quota(
            connection,
            SettleQuotaRequest(
                tenant_id=TENANT_ID,
                project_id=PROJECT_ID,
                operation_id=OPERATION_ID,
                target_send_state=SendState.CONFIRMED_NOT_SENT,
                reason_code="unsafe_direct_release_v1",
            ),
        )

    assert connection.rollbacks == 1
    assert LOAD_RESERVATION_SQL not in [query for query, _params in connection.calls]


def test_send_unknown_can_never_be_released() -> None:
    connection = _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_SQL: lambda _params: (
                (_operation_row(send_state=SendState.SEND_UNKNOWN)),
            ),
        }
    )

    with pytest.raises(
        QuotaV2Error,
        match="no_resend_send_truth_cannot_release_quota",
    ):
        settle_quota(
            connection,
            SettleQuotaRequest(
                tenant_id=TENANT_ID,
                project_id=PROJECT_ID,
                operation_id=OPERATION_ID,
                target_send_state=SendState.CONFIRMED_NOT_SENT,
                reason_code="unsafe_unknown_release_v1",
            ),
        )

    assert connection.rollbacks == 1
    assert LOAD_RESERVATION_SQL not in [query for query, _params in connection.calls]


def test_reconciler_can_release_sending_only_with_owner_proof_and_dead_lease() -> None:
    connection = _settlement_fake(current_send_state=SendState.SENDING)
    proof_id = UUID("a0000000-0000-0000-0000-000000000001")
    connection.handlers[RECORD_NOT_SENT_PROOF_SQL] = lambda _params: ((proof_id,),)

    result = reconcile_quota(
        connection,
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
            reason_code="owner_proved_not_sent_v1",
            owner_gateway_revision="browser-owner-v1",
            owner_evidence_ref="evidence-owner-not-sent-1",
            evidence_hash="d" * 64,
        ),
    )

    assert result.action is ReconciliationAction.RELEASE
    assert result.send_state is SendState.CONFIRMED_NOT_SENT
    assert result.settlement is not None
    assert result.settlement.disposition is ReservationDisposition.RELEASED
    assert result.settlement.idempotent is False
    queries = [query for query, _params in connection.calls]
    assert queries.index(RECORD_NOT_SENT_PROOF_SQL) < queries.index(UPDATE_OPERATION_SEND_STATE_SQL)
    assert connection.commits == 1


def test_reconciler_defers_sending_without_owner_evidence_and_never_releases() -> None:
    connection = _FakeConnection(
        {
            SET_TENANT_SQL: _none,
            LOAD_OPERATION_SQL: lambda _params: ((_operation_row(send_state=SendState.SENDING)),),
        }
    )

    result = reconcile_quota(
        connection,
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.NONE,
            lease_terminated=True,
            reason_code="timeout_scan_v1",
        ),
    )

    assert result.action is ReconciliationAction.DEFER
    queries = [query for query, _params in connection.calls]
    assert LOAD_RESERVATION_SQL not in queries
    assert UPDATE_OPERATION_SEND_STATE_SQL not in queries
    assert UPDATE_EFFECT_TERMINAL_SQL not in queries


def test_not_sent_owner_evidence_requires_opaque_durable_proof_identity() -> None:
    with pytest.raises(QuotaV2Error, match="not_sent_reconciliation_proof_required"):
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
            reason_code="owner_proved_not_sent_v1",
        )

    with pytest.raises(QuotaV2Error, match="invalid_owner_evidence_hash"):
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
            reason_code="owner_proved_not_sent_v1",
            owner_gateway_revision="browser-owner-v1",
            owner_evidence_ref="evidence-owner-not-sent-1",
            evidence_hash="not-a-sha256",
        )

    with pytest.raises(QuotaV2Error, match="unexpected_not_sent_reconciliation_proof"):
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.NONE,
            lease_terminated=True,
            reason_code="timeout_scan_v1",
            owner_gateway_revision="browser-owner-v1",
        )

    with pytest.raises(QuotaV2Error, match="invalid_owner_evidence_ref"):
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
            reason_code="owner_proved_not_sent_v1",
            owner_gateway_revision="browser-owner-v1",
            owner_evidence_ref="https://owner.invalid/proof",
            evidence_hash="d" * 64,
        )

    with pytest.raises(QuotaV2Error, match="invalid_owner_gateway_revision"):
        ReconcileQuotaRequest(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            operation_id=OPERATION_ID,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
            reason_code="owner_proved_not_sent_v1",
            owner_gateway_revision="g" * 129,
            owner_evidence_ref="evidence-owner-not-sent-1",
            evidence_hash="d" * 64,
        )


@pytest.mark.parametrize(
    ("send_state", "effect", "disposition"),
    [
        (
            SendState.CONFIRMED_SENT,
            LedgerEffectKind.SETTLE_CONSUMED,
            ReservationDisposition.CONSUMED,
        ),
        (
            SendState.SEND_UNKNOWN,
            LedgerEffectKind.SETTLE_UNKNOWN,
            ReservationDisposition.UNKNOWN_CONSUMED,
        ),
        (
            SendState.CONFIRMED_NOT_SENT,
            LedgerEffectKind.RELEASE,
            ReservationDisposition.RELEASED,
        ),
    ],
)
def test_settlement_truth_table(
    send_state: SendState,
    effect: LedgerEffectKind,
    disposition: ReservationDisposition,
) -> None:
    assert settlement_effect(send_state) == (effect, disposition)


@pytest.mark.parametrize("send_state", [SendState.NOT_SENT, SendState.SENDING])
def test_nonterminal_send_truth_cannot_be_directly_settled(send_state: SendState) -> None:
    with pytest.raises(QuotaV2Error, match="non_terminal_send_state_has_no_settlement"):
        settlement_effect(send_state)


def test_sending_timeout_never_releases_without_owner_proof_and_dead_lease() -> None:
    assert (
        reconciliation_action(
            send_state=SendState.SENDING,
            owner_evidence=OwnerEvidence.NONE,
            lease_terminated=True,
        )
        is ReconciliationAction.DEFER
    )
    assert (
        reconciliation_action(
            send_state=SendState.SENDING,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=False,
        )
        is ReconciliationAction.DEFER
    )
    assert (
        reconciliation_action(
            send_state=SendState.SENDING,
            owner_evidence=OwnerEvidence.PROVED_NOT_SENT,
            lease_terminated=True,
        )
        is ReconciliationAction.RELEASE
    )
    assert (
        reconciliation_action(
            send_state=SendState.SENDING,
            owner_evidence=OwnerEvidence.ACKNOWLEDGEMENT_UNKNOWN,
            lease_terminated=False,
        )
        is ReconciliationAction.SETTLE_UNKNOWN
    )


def test_quota_blocker_output_has_no_scope_subject_or_sensitive_content() -> None:
    blocker = QuotaBlocker(
        bucket_hash="a" * 64,
        scope_kind=QuotaScopeKind.CREDENTIAL,
        starts_at=datetime(2026, 8, 24, tzinfo=UTC),
        ends_at=datetime(2026, 8, 25, tzinfo=UTC),
        limit_units=1,
        reserved_units=1,
        consumed_units=0,
        unknown_units=0,
        requested_units=1,
    )
    rendered = repr(blocker.as_dict()).lower()
    assert blocker.available_units == 0
    assert "scope_subject" not in rendered
    assert "credential_id" not in rendered
    assert "token" not in rendered
    assert "cookie" not in rendered
    assert "password" not in rendered
