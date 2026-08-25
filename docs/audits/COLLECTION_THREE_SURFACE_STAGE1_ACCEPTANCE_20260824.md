# Three-surface collection: stage 0/1 acceptance evidence

Date: 2026-08-24

Contract set: `collection-three-surface-v2-20260824`

Scope: baseline, domain truth, canonical config/campaign identity, additive schema, and historical Web assignment plan

## Decision in force

- The Temporal `default` namespace retention is 30 days (`720h`).
- The production compose default is also `720h`; no Temporal service restart was performed in this implementation session.
- No completed v1 collection workflow history was available for export. The user accepted `unverified_legacy_v1_history_replay` as a known non-blocking rollout risk for the current development flow.
- Old v1 workflows are not restarted, retried, reopened, or resent. New execution work must use a separate v2 workflow type, payload, and task queue.
- No real provider, Web, or App collection request was sent.

## Protected baseline

The frozen legacy implementation prompt remained byte-for-byte unchanged:

- SHA-256: `6ea3bdec14175dd3b58a122840754feaeba0cba4777eadc9520bbced3001de1b`
- Lines: `5847`
- Bytes: `705881`

Production was inspected read-only before implementation. The database was still at `s06_0038_w_review`; the new migration was not applied. No collection workflow was visible in retained Temporal history, no v1 collection worker was active, and the collection task queues had no pollers.

## Canonical stage 1 contract

- `collection_surface` has exactly three values: `provider_api`, `consumer_web`, and `consumer_app`.
- Surface is distinct from run source, content provenance, platform, product variant, interaction mode, route, and physical resource.
- Config selects explicit `platform × surface × product_variant × mode` targets. Missing or unsupported capability declarations fail closed.
- Campaign admission first persists a compact immutable specification in `assembling`; it does not persist expanded slot membership in the campaign row.
- Logical cardinality is computed with checked arithmetic from the frozen measurement scope. V2 has no fixed 10,000-task product limit and does not import or call the V1 `run_service.py` path.
- Slots are derived in stable `target_key → question_slot_id → province_code → interaction_mode → sample_ordinal → role` order, carry a campaign-global zero-based ordinal and identity hash, and are written in bounded, independently committed chunks.
- Every chunk records its range, exact count, idempotency key, chunk hash, prior chain hash, and resulting chain hash. A retry must exactly match the committed batch and rows; gaps, overlaps, or identity/hash drift fail closed.
- Completion validates the expected/materialized counts and the stable membership chain before a separate short `assembling → frozen` state transition. Only a persisted `frozen` proof can produce a scheduler/workflow reference.
- Logical sample cardinality, database materialization chunk size, scheduler execution partition, and runtime concurrency are separate concepts. The Stage 1 workflow reference contains only constant-size campaign/partition/cursor/digest fields; actual partitioning and resource-governed concurrency remain later-stage work.
- A retry retains the original logical slot. Supplementary and top-up slots require an explicit reason and primary-slot link.
- Physical route, account, browser, device, credential, and relay identifiers are excluded from logical target and slot keys.

## Historical Web selection

The deterministic selector includes only:

1. native formal runs whose workflow ID starts with `geo-collection/` and whose source is `manual`, `schedule`, or `retry`; or
2. `legacy-history/` runs with a completed `legacy-geosys-sqlite` migration and a matching migrated `integration.legacy_id_map` collection-run record.

The read-only production baseline selected:

| Fact type                              |  Count |
| -------------------------------------- | -----: |
| collection runs                        |    498 |
| collection tasks                       |  3,104 |
| answers                                |  1,492 |
| answer analyses                        |  1,492 |
| analysis jobs                          |     21 |
| distinct answer-linked evidence assets | 12,399 |

All selected evidence assets matched the answer tenant and project; no selected evidence asset was shared across projects. Arbitrary `legacy-history/` rows without the required migration provenance are excluded.

The backfill plan writes only the nullable overlay fields `collection_surface`, `surface_assignment_basis`, and `legacy_contract_version`. It does not change old config bytes/hashes, `channel`, question text, answer text, raw capture content, campaign identity, slot identity, ordinal, or denominators. Dry-run is the default and always rolls back. Apply requires a tenant scope, the exact dry-run selection hash, a derived confirmation token, requester identity, and a stable batch key; it has not been run against production.

## Schema rehearsal

`s07_0001_surface_identity` was rehearsed on disposable PostgreSQL 16/pgvector instances:

1. upgrade from an empty database to the unique Alembic head;
2. verify all eight new identity/audit/materialization tables, FORCE RLS, and all six historical fact surface overlays;
3. downgrade to `s06_0038_w_review`;
4. upgrade again to `s07_0001_surface_identity`.

The first real PostgreSQL attempt exposed a PL/pgSQL syntax error that the SQL-rendering unit test could not detect: `IS DISTINCT FROM CASE ... END`. It was corrected to a parenthesized expression and covered by a regression assertion. The final upgrade/downgrade/re-upgrade sequence then passed. The database and Python implementations produced the same canonical 279,000-slot chain seed (`f11f038251ec324f7978afe1e451539db194dae440ba28f7d024150cb94b40b4`). All eight tables had FORCE RLS and tenant policies, and all six materialization/freeze trigger functions and triggers were present.

A second disposable database exercised the actual ORM/service path: compact header creation; a `[0,2)` chunk commit; exact replay of that committed chunk; resume with a different chunk size for `[2,5)`; count/digest finalization; and `assembling → frozen`. It ended with five expected/materialized/persisted slots, two completed batches, and no duplicate rows. A direct attempt to freeze a zero-cursor campaign was rejected by the database and left it `assembling`.

All disposable containers and anonymous volumes were removed. Production and the persistent development database remained at `s06_0038_w_review`; neither contains `platform.collection_campaign` or `platform.collection_campaign_materialization_batch`.

A final empty-database compatibility run also reached the repository's existing Alembic head (`s08_0001_service2_all_u`) through the corrected `s07_0001` and the already-present later revisions. This was migration-chain verification only; it did not expand or certify Stage 2/4 runtime behavior.

## Focused verification

- Stage 0/1 unit suite: `76 passed`, with one Alembic `path_separator` deprecation warning.
- The synthetic high-cardinality test streamed all `279,000` slots twice with chunk sizes `257` and `4,096`; counts, sampled identities, and the final digest were identical. Peak process RSS for the isolated identity test run was about `109 MiB`, and no expanded slot collection exists on the blueprint or in its specification JSON.
- Persistence tests: five cases covering one transaction per bounded chunk, lost commit acknowledgement, rollback/resume without gaps or duplicates, gap/overlap/drift rejection, incomplete freeze rejection, and idempotent final acknowledgement.
- Ruff lint and format checks: passed for all correction files.
- Strict Mypy: passed for `identity_v2.py` and `campaign_materialization_v2.py`.
- `git diff --check`: passed.

## Remaining gates

- Real v1 Temporal history replay remains unverified under the explicit temporary user decision above.
- The migration and dry-run evidence do not authorize production migration or historical backfill apply.
- The Stage 1 persistence service is not wired into a production router, scheduler, outbox, or Temporal workflow. Execution partition creation, Continue-As-New behavior, capacity/resource/relay/quota-controlled concurrency, and real dispatch remain Stage 2/4 work.
- Capability registry persistence, formal binding, typed grants, quota reservation/ledger, resource leases/fences, and submission operation belong to Stage 2 and later.
- No surface is live merely because its contract or fake implementation exists. Production execution remains fail closed until the complete governance chain is ready and real collection is separately authorized.
