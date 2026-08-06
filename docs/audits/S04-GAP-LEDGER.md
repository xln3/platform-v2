# S04 verified gap ledger

Audited: 2026-07-27 (Asia/Shanghai)
Rule: status documents are leads, not proof. `complete` requires current code and every applicable §18 gate.

Status vocabulary:

- `verified`: the scoped requirement has current code and runtime evidence.
- `partial`: substantive implementation exists, but an explicit gate remains.
- `external-gate`: completion requires an account owner, customer terminal, legal authorization or live
  platform credential that is not present and must not be fabricated.
- `not-applicable-no-source-sample`: the legacy source has no row for a required comparison; absence is recorded
  but cannot prove behavior on populated data.

## §17.1 Foundation and contracts

| Requirement                             | Status   | Current evidence or remaining gap                                                                                                                                                                                                                                                                                                       |
| --------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| V2 monorepo/toolchain                   | verified | 12 packages lint/type/test/build; Python 173/173; 168 TypeScript/Node tests; mypy 98 files.                                                                                                                                                                                                                                             |
| Four React applications/shared packages | verified | 411/411 desktop/tablet/mobile E2E and 45/45 real-session production route/page checks.                                                                                                                                                                                                                                                  |
| FastAPI modular monolith                | verified | Shared production API mounts S01/S02/S03 contracts; live probes and role sessions pass.                                                                                                                                                                                                                                                 |
| Generated OpenAPI client                | verified | Regeneration and drift guard pass at 97 paths; OpenAPI/generated hashes are recorded in `contracts/generated-manifest.json`, and the current frontend contract guard passes.                                                                                                                                                            |
| Dependency/deploy topology              | verified | All 13 isolated production containers run; PostgreSQL/pgvector, ClickHouse, Temporal, MinIO, Redis, Vault, Prometheus, Alertmanager, Loki, Alloy, OTel and Grafana are live; API, three Workers, business exporter and safe alert receiver are active.                                                                                  |
| CI/local quality matrix                 | partial  | The previously nonmatching path filters/working directory were fixed. Five root jobs now cover fresh pgvector integration+migration, TypeScript, contracts/release guards, 216-case E2E and smoke; the complete local equivalent and CI drift guard pass. No Git remote exists, so a hosted run remains unavailable and is not claimed. |

Evidence: `full-quality-certification.json`, `production-browser-acceptance.json`,
`production-identity-certification.json`, `production-runtime-data-counts.json`.

## §17.2 Data and storage

| Requirement              | Status   | Current evidence or remaining gap                                                                                                                                                                                                                                                                                                                                        |
| ------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| PostgreSQL/Alembic       | verified | Production is at `s04_0029`; all 85 tenant business tables force RLS and the API/Worker runtime roles cannot bypass it.                                                                                                                                                                                                                                                  |
| tenant/RBAC isolation    | partial  | Populated two-tenant and report/customer/Intelligence role tests pass. Production actor certification covers 18 fields with zero external-subject residue; missing platform user projection fails closed. OIDC signature/claim verification, browser S256 PKCE and hashed bindings reject actor-header impersonation, but live IdP/passkey and four real roles are open. |
| ClickHouse/outbox/replay | verified | All migrated events projected; forced redelivery is idempotent; one-million-row capacity and restore pass.                                                                                                                                                                                                                                                               |
| MinIO CAS/DLP/hash       | verified | 533 migrated assets hash-verified; tamper/DLP tests, 32 MiB capacity and restore pass.                                                                                                                                                                                                                                                                                   |
| pgvector/FTS             | verified | Authoritative production PostgreSQL image includes pgvector; real integration suite passes.                                                                                                                                                                                                                                                                              |
| backup/restore           | partial  | PostgreSQL/ClickHouse/MinIO restore passes. Production Vault Raft and authority backups are root-only and hash recorded; stop/start/unseal and post-restart crypto pass. Same-host mechanics block retained ciphertext after key deletion/recreation, while independent custody and authorized-profile deletion remain open.                                             |

Evidence: `production-backup-restore.{json,md}`, `production-capacity.{json,md}`,
`production-evidence-migration*.json`, `production-runtime-data-counts.json`.

## §17.3 Workflows, collection and account/session custody

| Requirement                                 | Status        | Current evidence or remaining gap                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Temporal topology and Workers               | verified      | Production Temporal plus main/S02 Workers active.                                                                                                                                                                                                                                                                                                                                                                                                             |
| crash/failure/retry/Signal/cancel/recovery  | verified      | Activity retry, heartbeat, real Worker stop/restart, duplicate Signals and durable cancellation pass. Lease cleanup now runs from a retrying idempotent `finally` across success, Activity failure, external cancellation and Continue-As-New; production has zero expired-unreleased leases.                                                                                                                                                                 |
| S01→S02 workflow chain                      | verified      | API→collection workflow→Activity trace plus evidence/report/investigation real-service suites pass. The migrated slice now proves 146/146 answers carry run/config lineage and V2 analyses, and all four historical completion events converge once with a zero-write rerun.                                                                                                                                                                                  |
| fenced lease/revocation/break-glass/DLP     | verified      | Focused 60-test security matrix passes against real dependencies where applicable.                                                                                                                                                                                                                                                                                                                                                                            |
| AS-01 registry/owner/scope/admission        | partial       | Registry/admission code and L0 evidence exist; the 13,063 legacy profile candidates lack authoritative owner/platform/scope mapping.                                                                                                                                                                                                                                                                                                                          |
| AS-02 encrypted Profile Vault               | partial       | Production Vault 2.0.3 uses TLS 1.3, persistent Raft, 2-of-3 Shamir and separated runtime/provision/delete credentials. Fenced profile DEK rekey, admitted-account KEK rotation, old-ciphertext recovery, v2 rewrap and minimum-decryption rollback rejection pass against the real Vault; synthetic delete/restart/backup non-reactivation also pass. No authorized per-account import/cutover or independent organizational custody exists.                 |
| AS-03 fenced lease/isolation                | partial       | Lease competition/bindings, failure/external-cancel cleanup, profile-bound writes, atomic version-release and monotonic replacement fencing pass; real account/profile/device/egress co-location cannot be proven without an admitted live account.                                                                                                                                                                                                           |
| AS-04 lifecycle/revocation                  | partial       | Success/failure/cancel/Continue-As-New lease release, revocation, first-result Signal idempotency, persisted pairing/task expiry and key purge pass; authorized-profile backup deletion propagation remains open.                                                                                                                                                                                                                                             |
| AS-05 customer terminal/challenges          | partial       | A real headful Chromium fixture proves MV3 loading, stable signed ID, exact-origin permission UI, non-extractable/export-rejected persistent Ed25519 key storage, signed backend pairing and minimal completion; the result remains `awaiting_platform_probe`. Production serves a hash-certified CRX3 whose signing key and backup are root-only. An authorized customer install and native OTP/QR/Push/passkey/face/captcha canaries remain external gates. |
| AS-06 safe Operations UI                    | verified      | The prior live fixture/empty-state path was removed. Operator/admin pages consume one real, bounded `/api/v2/operations/lifecycle` projection; PostgreSQL tenant/role tests, focused E2E 30/30 and production overview/sessions/interventions/events checks 12/12 pass without secret surfaces.                                                                                                                                                               |
| AS-07 inventory/import/shadow/cutover/purge | external-gate | Metadata-only inventory exists; owner/platform/scope authorization is required before import or plaintext destruction.                                                                                                                                                                                                                                                                                                                                        |
| AS-08 platform capability canary            | external-gate | No live AI/read/publish credentials are present. Status is `adapter_ready`, never live/publish verified.                                                                                                                                                                                                                                                                                                                                                      |
| AS-09 DLP/incident response                 | partial       | Code, binary fail-closed tests and secret-free evidence pass; quarantined legacy HAR/profile estate cannot be purged before AS-07 authorization.                                                                                                                                                                                                                                                                                                              |
| AS-10 customer/legal authorization          | external-gate | No authoritative owner/legal approval artifact exists for the discovered accounts/profiles.                                                                                                                                                                                                                                                                                                                                                                   |

Evidence: `production-workflow-resilience.{json,md}`, `security-regression.json`,
`legacy-sensitive-inventory.json`, `real-gateway-evidence-runtime.json`.

## §17.4 Analytics, evidence, reports and Anti-GEO

| Requirement                                       | Status   | Current evidence or remaining gap                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| analytics/scoring/citations/KPI                   | verified | V2 rebuilt 146 analyses and 2,754 citations; all 146 migrated answers now retain collection run/config lineage, four completion events are acknowledged from verified rebuilt truth, and persisted reconciliation is zero-diff.                                                                                                                                                                                                                                                                                                                                                |
| evidence/history/packages                         | verified | 533 admitted objects pass DLP/source hash/target hash; 149 HAR and 6 captcha objects remain quarantined by policy.                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| report workflow/formats/review/delivery           | partial  | Immutable report revisions now use a required hash-only idempotency contract, server-side frozen facts, evidence binding and lock/resume rendering for HTML/DOCX/PDF/XLSX. Analyst authoring and reviewer/publisher/deliverer permissions are separated; only an active same-tenant customer recipient can read the explicitly delivered published projection. Production schema/source certification passes at `s04_0027`, but legacy/V2 production contain zero persistent report rows, so populated migration shadow and real-customer delivery acceptance are unavailable. |
| Anti-GEO claim→multi-source→score→verdict→package | verified | API, Temporal, persistence and four-application E2E paths pass. Analysts author/appeal, reviewers verdict/resolve, and appeal resolution requires a second reviewer independent of both submitter and latest verdict reviewer.                                                                                                                                                                                                                                                                                                                                                 |
| recommendation calibration                        | partial  | `s04_0029` deploys a five-table evidence-bound dataset/evaluation/model-admission registry with ENABLE+FORCE RLS, independent dataset approval/admission, source-retention and training/holdout cluster isolation. The generated API/client and three-viewport calibration UI pass. Production has zero approved external label sets and zero qualified admission chains, so calibration/live admission is not claimed.                                                                                                                                                        |

Evidence: `production-derived-rebuild.json`, `production-target-reconciliation.{json,md}`,
`production-evidence-migration*.json`, `tests/s04-evidence/e2e-results-s04-0029-final.json`,
`tests/s04-evidence/anti-geo-evaluation-boundary.json`,
`tests/s04-evidence/production-report-delivery-confirmation.json`,
`tests/s04-evidence/production-report-authoring.json`,
`tests/s04-evidence/production-actor-identity.json`.

## §17.5 Four applications

All four applications consume `/api/v2`, fail closed without an explicit session, and have no production
fixture identity payload. Playwright writes fixtures only to `build-e2e/`; Nginx serves `build/`. A post-build
guard enforces the boundary. A 29-workspace production runtime scan also reports zero fixture/mock markers,
console errors, page errors and failed requests. Production real-session acceptance is 45/45 with zero console errors, page
errors, failed requests or HTTP error responses.

Status: `verified` for the available V2 business records. External-platform login/read/publish remains governed
by AS-08 and is not inferred from application readiness.

## §17.8 Operations, migration and production

| Requirement                                 | Status                          | Current evidence or remaining gap                                                                                                                                                                                                                                                         |
| ------------------------------------------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OTel API→workflow→activity                  | verified                        | One trace ID contains API request, Workflow and Activity spans.                                                                                                                                                                                                                           |
| Prometheus/Grafana/Loki                     | verified                        | Four Prometheus targets are up; 12 business rules load; a real alert traversed Prometheus→Alertmanager→allowlist receiver→Loki, then resolved after its lineage defect was repaired. Current firing alerts and admission backlog are zero; Grafana dashboard v2 has four business panels. |
| source-to-target migration/watermark/resume | verified                        | Controlled production migration, repeated run and injected interruption recovery pass. The lineage repair reconciled 146 answers and four completion events from rebuilt V2 truth; the immediate second run wrote zero and the current outbox backlog is zero.                            |
| old/V2 shadow reconciliation                | verified                        | Machine-readable JSON/Markdown cover populated task, answer, eligibility, citation, KPI and evidence slices with zero differences and zero approvals.                                                                                                                                     |
| report artifact shadow                      | not-applicable-no-source-sample | Legacy report table has zero rows; this is not represented as positive populated-data proof.                                                                                                                                                                                              |
| production deployment/roles/visuals         | partial                         | Additive V2 routes and 45 admin-session screenshot/check combinations are verified; four other real human roles are unavailable.                                                                                                                                                          |
| legacy coexistence                          | verified                        | Baseline-plus-additive-include evidence; `/portal`, `/ops`, `/client`, `/api/health` return 200 without redirects and `geosys.service` remains active.                                                                                                                                    |
| backup deletion propagation                 | partial                         | Real Vault mechanism certification proves retained synthetic backup ciphertext cannot reactivate after account-key deletion/recreation; production proof remains unavailable because AS-07 profiles were not authorized for import.                                                       |

Consolidated release evidence: `tests/s04-evidence/production-release-s04-0029.json`.

## Duplicate logic, drift, mocks and sensitive material

- The first `s04_0029` production browser run rejected 12 Operations pages because the live shell discarded its
  API contract and substituted fixture/empty state. That branch was removed; the shared generated client now
  projects the real lifecycle endpoint, while explicit fixtures remain only in the E2E harness. The repeated
  production browser matrix is 45/45 and the mock scan is 29/29.
- The Anti-GEO evaluator-to-production gap was closed with persisted evidence-bound governance rather than a
  synthetic success claim. The registry and policy guards pass at `s04_0029`; the absent independently approved
  external dataset remains an external sample gate.
- The CI certification tool's hard-coded 216-case expectation drifted from the current 411-case suite. It now
  resolves the exact E2E artifact named by full-quality evidence and verifies expected/unexpected/skipped/flaky
  counts dynamically; its 12 assertions and the independent CI guard pass.
- S02 router omission, migration role drift, non-Crockford IDs, non-executable migrated snapshots, tenant-global
  collection lookup, missing outbox worker, ClickHouse bind conflict and intermittent FORCE-RLS report-artifact
  parent lookup were found and fixed. The report parent row is now key-share pinned through transaction commit.
- The external-subject/platform-user-ID drift and coarse report/Intelligence permissions were found by current
  source and PostgreSQL integration inspection rather than status claims. `s04_0026` normalized known production
  actor fields; report delivery/read isolation and independent human arbitration are enforced in API, generated
  client, three-viewport UI paths and real-PostgreSQL tests.
- The S03 handoff's report editor was still local-only despite a live report read surface. `s04_0027` adds the
  missing immutable revision write contract, hash-only replay identity, evidence binding and resumable artifact
  rendering; generated client, Report Studio, PostgreSQL/MinIO integration, production schema certification and
  three-viewport E2E now cover the same boundary. This closes the source-owned gap but does not manufacture a
  populated production report.
- The observability status was reopened because Prometheus/Grafana/Loki had no loaded business rules or
  notification route. `s04_0028` adds a worker-only 12-row aggregate, five bounded query indexes, 12 alert rules,
  Alertmanager, a label-allowlisting receiver and four Grafana panels. The first real alert then exposed that
  migrated answers lacked run/config lineage: the migrator now backfills those immutable links, acknowledges a
  legacy completion event only after every task answer has a V2 rebuilt analysis, and proves a zero-write rerun.
- Production fixture identity payloads were removed. Test-only fixtures are explicit and isolated under
  `build-e2e/`; `check_production_bundles.py` prevents release regression and
  `production-mock-scan.json` verifies all 28 live workspaces.
- Production profile custody refuses the development-only LocalKms. This prevents creation/use of locally
  recoverable wrapped DEKs. Production Vault Transit is now configured with separated systemd credentials and
  live mechanics evidence. Same-host configuration does not substitute for independent KMS/HSM custody or prove
  deletion propagation for an authorized migrated profile.
- No raw secret is included in inventory or evidence. A historical service-journal DSN exposure and the current
  diagnostic SQLAlchemy/psycopg mismatch were each remediated without reproducing the value; PostgreSQL
  credentials were rotated after each event. The current rotation uses distinct credentials for all three
  configured roles and passes 6/6 machine certification:
  `tests/s04-evidence/production-postgres-credential-rotation.json`.
- Legacy sensitive candidates remain protected in restricted backups and are not guessed, imported or destroyed.

## §18 unified completion gate

The following gates are verified: current code quality, real dependencies, workflow partial failures, four-app
E2E/visuals, production deployment, five-role automated authorization behavior, one-role real human acceptance, observability, performance/capacity,
backup/restore, idempotent migration, shadow reconciliation and unchanged legacy routes.

The unified gate is **not complete** because:

1. the deployed OIDC resource-server, browser S256 PKCE and hashed binding lifecycle have no production
   issuer/JWKS client registration, cookie-key credential or real bindings, so production remains on the
   read-only legacy HttpOnly-session bridge; only `admin` is available and customer/operator/analyst/reviewer
   human production acceptance is unverified;
2. AS-01/02/03/04/05/07/09/10 require authoritative profile ownership, customer-terminal and legal evidence;
3. AS-08 has no authorized live external-platform credentials;
4. backup deletion propagation for an actually imported per-account profile is unproven;
5. no independently approved external Anti-GEO calibration label set or qualified model-admission chain exists;
6. legacy report data is empty, so a populated report migration-shadow comparison cannot be performed.

The persistent goal remains active. None of these gaps may be relabelled “complete” using adapter readiness,
fixtures, metadata inference or documentation alone.
