# Three-surface collection: Stage 2 acceptance evidence

Date: 2026-08-24

Contract set: `collection-three-surface-v2-20260824`

Scope: formal bindings, typed execution grants, resource leases/fences, multi-scope quota reservation, durable not-sent proof, RLS, and runtime-role ACL boundaries

## Conclusion

Stage 2 is complete at the schema, domain-contract, pure-service, quota-persistence, and authorization-boundary level. It does not claim that an API/Web/App adapter, physical resource owner, external submit, scheduler, or Temporal workflow is wired or live.

No real provider, Web, or App request was sent. No migration was applied to the shared development or production database, and no service was deployed or restarted.

The final migration file SHA-256 is `e91240fe9554fd3d92b8077c8630f5d84f6d3ce6da0e460631bc2a7ab2f5177a`. The rendered upgrade SQL SHA-256 is `93be864950fc5356732d268432c3f4d5291f11df357a307875b10fb5f3cd5588`.

## Governance chain

An irreversible action is admissible only when one exact, tenant/project-scoped chain is current:

```text
active config target
  -> complete frozen campaign/target/leg/slot
  -> active formal binding revision and required capability
  -> one operation-level quota reservation with every required effect reserved
  -> every required formal resource mapping with a current lease/fence
  -> issued typed API/Web/App grant
  -> fresh resource-owner authorization for NOT_SENT -> SENDING
```

The domain service rejects cross-tenant/project drift, surface/subtype mismatch, old binding or operation generations, future activation/acquisition timestamps, stale fences, missing or terminal quota effects, incomplete resource mappings, and unsafe execution material.

`authorize_irreversible_action()` is deliberately a pure assertion boundary. Its result is not a bearer token. A later physical owner must load fresh state under a row lock or atomic CAS, persist `NOT_SENT -> SENDING`, and serialize the side effect. Stage 2 does not claim that runtime owner/CAS wiring is complete.

## Formal bindings and typed grants

- API, Web, and App use a shared envelope with strict discriminated subtypes.
- A binding freezes exact capability/quota registries, policy revisions, target dimensions, approval/readiness timestamps, resource mappings, and non-usable secret-reference metadata.
- Required resource kinds are policy facts; a business role such as `primary_browser` remains distinct from `browser_owner` resource kind.
- Each grant resource freezes registration ID, public ID, kind, business role, ordinal, mapping revision, capacity unit, lease, owner handle, and fence generation.
- API/Web/App opaque handles must match their binding and approved resource mapping. Raw secrets, cookies, passwords, reusable device IDs, URLs, host/port endpoints, and bare IPv4/IPv6 endpoints are rejected.
- `crowd-assistant-apk` remains an assistance tool and cannot be declared as a Consumer App collector.

## Resource ownership and fencing

- Capacity is explicit per physical resource and capacity unit; a region is not a global mutex.
- Genuine acquisition increments the capacity-unit fence monotonically.
- Exact acquisition replay is idempotent only when every immutable request field matches.
- A stale lease ID or generation heartbeat is a no-op. An active heartbeat cannot extend a lease beyond its active binding window.
- Grant issuance requires every required lease to be active, current, already acquired, and valid through the grant expiry.
- Formal resource registrations and leases are durable and cannot be deleted or have their identity rewritten.

The in-memory acquire/heartbeat implementation is a CAS specification for owner code; the database-backed owner loop and physical handle quarantine/recovery remain later-stage work.

## Multi-scope quota and send truth

- Applicable scopes are loaded from the active binding and quota registry; callers cannot submit a reduced bucket set or their own limits.
- Day/week/year, provider/account/credential/project/contract, platform-surface, and mode scopes use canonical keys and lock order.
- One operation owns one reservation envelope with multiple exact effects. `expected_effect_count`, effect-set hash, policy ID, bucket hash, units, and state are validated end to end.
- Every applicable bucket is reserved in one short transaction. Canonical advisory locks and bucket row locks provide all-or-nothing admission without locking the whole binding or region.
- Settlement follows durable send truth, not answer availability. `CONFIRMED_SENT` consumes, `SEND_UNKNOWN` consumes as unknown, and neither can release.
- Direct `SENDING -> CONFIRMED_NOT_SENT` and direct quota release are rejected. Release from `SENDING` requires an append-only accepted owner proof, at least one formal lease, and authoritative confirmation that every lease is terminated.
- Deferred database conservation checks reconcile bucket projections, reservation/effect state, exact binding scope coverage, effect digest, and append-only ledger events at transaction commit.
- Provider-custom windows remain fail closed unless a trusted resolver supplies a unique boundary containing the operation timestamp.

## Database hardening and ACL

`s07_0002_execution_governance` directly follows `s07_0001_surface_identity`. It adds 24 project-scoped governance tables and nullable, discriminated V2 extensions to legacy resource registration/lease rows.

All 24 new tables enable and force tenant RLS. Composite foreign keys retain tenant/project identity across capability, binding, operation, quota, resource, proof, and grant rows. Partial V2 legacy shapes fail closed through explicit `IS NOT NULL` checks; legacy rows remain untouched.

The migration first revokes Stage 2 table/function privileges from `PUBLIC`, `geo`, `geo_api`, and `geo_worker`, then applies an explicit least-privilege matrix. API and worker roles have no DELETE and cannot update identity columns. Only `geo_worker` can execute the durable not-sent proof function. The runtime-role provisioning tool reapplies and verifies the same matrix after its older schema-wide grants, preventing later privilege re-expansion.

PostgreSQL object owners/superusers remain an administrative trust boundary and cannot be constrained by ordinary object ACLs.

## Defects found and closed during acceptance

Independent reviews found and the implementation closed these gaps before acceptance:

- registry activation timestamps were accidentally protected against their own legal `frozen -> active` transition;
- direct inserts could begin in terminal binding/grant/quota/lease states;
- a normal settlement path could release quota from `SENDING`;
- governance modeled one quota reservation per bucket while persistence used one envelope with many effects;
- leases were checked by kind but not by exact binding resource identity;
- future binding activation and lease acquisition could pass pure-service checks;
- runtime role provisioning could silently restore broad write/delete grants;
- `SENDING -> CONFIRMED_NOT_SENT` lacked durable proof and terminated-lease gates;
- quota counters, effects, reservations, and ledger rows lacked a deferred conservation proof;
- nullable legacy V2 shapes could exploit PostgreSQL CHECK three-valued logic;
- grant DB issuance did not fully pin active config, frozen campaign, binding policy, resource mapping revision, or expiry windows;
- endpoint DLP missed underscore hostnames, whitespace variants, and unbracketed IPv6;
- quota reads unnecessarily locked active binding/registry rows and serialized unrelated operations.

## Verification

- Root Stage 1 + Stage 2 regression: `208 passed, 2 skipped`, with two existing Alembic `path_separator` deprecation warnings. The skipped cases are the two opt-in PostgreSQL quota tests when no isolated DSN is present.
- Governance + quota unit suite: `105 passed`.
- Governance suite after final DLP hardening: `73 passed`.
- Migration/runtime-role ACL unit subset: `27 passed`.
- Isolated PostgreSQL 16 service suite: `32 unit + 2 real-service integration = 34 passed`.
- Fresh PostgreSQL migration rehearsals passed base-to-`s07_0002`, downgrade to `s07_0001`, and re-upgrade to `s07_0002`.
- Real `geo_worker` execution proved concurrent contention for the final multi-scope unit admits exactly one operation, leaves no partial reservation, preserves idempotency, and maintains bucket/effect/ledger conservation.
- Real reconciliation proved a caller claim cannot release while a formal lease is active; after authoritative lease termination, durable proof, release, and replay pass.
- Real ACL checks proved no runtime DELETE or identity UPDATE, no API/public proof execution, and correct worker-only proof execution after broad-role provisioning was reapplied.
- PostgreSQL negative cases rejected partial-null V2 rows, raw endpoint handles, forged bucket/ledger state, unproved not-sent transitions, and leases/grants outside binding windows.
- Ruff lint and format checks passed for all Stage 2 files.
- Strict Mypy passed for governance, quota, runtime-role, and integration modules.
- `git diff --check` passed.
- All disposable PostgreSQL containers and anonymous volumes were removed.

## Remaining stages and live boundary

- Stage 3 must persist submission-operation transitions through a real owner/gateway, wire fake API/Web/App adapters, separate send/capture/analysis truth, and prove crash/ACK-unknown/no-resend behavior end to end.
- Stage 4 must add constant-size V2 Temporal payloads, execution partitions, Continue-As-New, region/relay generation, scheduling, pause/resume/cancel, and old/new worker isolation.
- Stage 5 must carry frozen surface identity through capture, answer, analytics, comparison, reporting, and four-denominator views.
- Stage 6 must perform complete compatibility, shadow, canary, and rollout acceptance. A live canary still requires separate user authorization and real ready bindings/resources.
- Legacy V1 Temporal history replay remains unverified under the user's accepted temporary decision. Old V1 tasks are not restarted, retried, reopened, or resent.
