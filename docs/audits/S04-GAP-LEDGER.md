# S04 verified gap ledger

Audited: 2026-07-24 (Asia/Shanghai)  
Rule: status documents are leads, not proof. `PASS` requires current code plus section 18 evidence.

Status vocabulary:

- `implemented-unified-gates-missing`: code exists, but one or more plan §18 gates are absent.
- `partial`: only part of the planned capability exists.
- `missing`: no substantive implementation was found.
- `external-gate`: code can be tested locally, but live owner/legal/platform evidence is unavailable.

## §17.1 Foundation and contracts

| Requirement                     | Verified status                   | Current evidence and gap                                                                                                             |
| ------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| V2 monorepo/toolchain           | implemented-unified-gates-missing | Workspace, Turbo, Python toolchain and four apps exist; production evidence is absent.                                               |
| Four React apps/shared packages | implemented-unified-gates-missing | Builds/tests and visual baselines exist; business fixtures remain and production is absent.                                          |
| FastAPI modular monolith        | implemented-unified-gates-missing | S01 and S02 routers are now mounted; fresh integrated runtime/role tests remain.                                                     |
| Generated OpenAPI client        | partial                           | 69-path integrated contract/client and drift guard pass; four apps do not consume all business APIs.                                 |
| Compose/test infrastructure     | partial                           | PostgreSQL/pgvector, ClickHouse, Temporal, MinIO and Redis are healthy; API/workers/apps/observability are not all Compose services. |
| CI quality matrix               | partial                           | lint/type/unit/build jobs exist; full real integration/E2E/deploy certification is not demonstrated in CI.                           |

## §17.2 Data and storage

| Requirement                      | Verified status                   | Current evidence and gap                                                                                 |
| -------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------- |
| PostgreSQL/Alembic               | partial                           | S01/S02 parallel heads exist; required S04 merge revision and production migration are missing.          |
| tenant/RBAC/RLS                  | implemented-unified-gates-missing | S01 tests exist; integrated S02 cross-tenant/role coverage and production role acceptance remain.        |
| ClickHouse pipeline              | implemented-unified-gates-missing | S02 real tests and tables exist; backup/rebuild/production evidence remains.                             |
| MinIO CAS evidence               | implemented-unified-gates-missing | S02 CAS/tamper tests exist; production policy, backup and restore proof remain.                          |
| pgvector/FTS                     | partial                           | Tested override uses pgvector; base Compose still needs one authoritative topology and production proof. |
| outbox/replay                    | partial                           | S02 implementation exists; full cross-domain partial-failure and production replay proof remain.         |
| backup/restore/hash verification | missing                           | No V2 end-to-end backup/restore run or evidence manifest found.                                          |

## §17.3 Workflows and collection

| Requirement                                          | Verified status | Current evidence and gap                                                                                                                 |
| ---------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Temporal dev/production topology                     | partial         | Development Temporal is healthy; production topology/deployment is absent.                                                               |
| Collection/intervention/session/revocation workflows | partial         | S01 code/tests exist; fresh integrated crash/failure/signal/cancel/recovery matrix remains.                                              |
| Analysis/evidence/report/investigation workflows     | partial         | S02 code/tests exist; real S01-gateway EvidenceCapture run is still missing.                                                             |
| Worker isolation/lease/retry/recovery                | partial         | Fenced lease tests exist; isolated runner/tmpfs/network boundary and production fault evidence remain.                                   |
| AS-01 registry/owner/scope/admission/review date     | partial         | Models/UI exist; complete platform catalog, current rules review and execution denial audit remain.                                      |
| AS-03 fenced lease/isolation/co-location             | partial         | Lease and bindings exist; isolation and account/profile/device/egress co-location need runtime proof.                                    |
| AS-04 lifecycle/revocation                           | partial         | Workflows exist; backup deletion propagation and full integrated recovery remain.                                                        |
| AS-05 customer terminal/pairing/challenges           | partial         | Contract/UI and deterministic flows exist; terminal Agent/extension and native OTP/QR/Push/passkey/face/captcha verification are absent. |
| AS-08 per-platform capability canary                 | external-gate   | Only `adapter_ready` deterministic fixture evidence exists; no live platform credential/approval.                                        |

## §17.4 Analytics and evidence

All scorer/metric, answer/citation, KPI trace, evidence capture/history/package, and shared-kernel requirements have
substantive S02 implementations. Their status is `partial`: integrated API/runtime/client E2E, production
deployment, real roles, production screenshots and §18 evidence are absent. Recommendation remains explicitly
experimental and may not be represented as calibrated.

## §17.5 Four applications

All four application shells and planned workspaces have component/visual evidence, but the section is `partial`.
Customer analytics/answers/evidence/reports and Report/Intelligence business records still contain labelled
contract fixtures. Operations also retains a handwritten API boundary. Production V2 URLs and role acceptance do
not exist.

## §17.6 Anti-GEO

Graph, feature families, multi-source clustering, probabilistic scoring, human review/appeal and evaluation code
exist and have S02 tests. Status is `partial`: integrated real API/browser workflow, production evidence package,
approved data/evaluation provenance and §18 gates remain.

## §17.7 Reports

Freeze, components, provenance audit, four artifact formats, versioning/review/publish/confirmation and optimization
models exist in S02/S03. Status is `partial`: frontend records are fixtures, integrated API/client E2E and production
role/publication evidence remain.

## §17.8 Security, operations and migration

| Requirement                                           | Verified status | Current evidence and gap                                                                                            |
| ----------------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------- |
| OpenTelemetry API→workflow→activity                   | missing         | No deployed collector/instrumented end-to-end trace evidence found.                                                 |
| Prometheus/Grafana/Loki/alerts                        | missing         | Not present in current Compose and no alert evidence found.                                                         |
| security/cross-tenant/sensitive tests                 | partial         | Strong S01/S02/S03 scoped tests exist; integrated exports/signed URLs/audit matrix remains.                         |
| AS-02 Profile Vault                                   | partial         | Envelope/rekey/delete tests exist; real KMS/HSM and backup recovery/cryptographic deletion proof remain.            |
| AS-06 Operations safe UI                              | partial         | UI exists; integrated production role acceptance remains.                                                           |
| AS-07 plaintext inventory/import/shadow/cutover/purge | missing         | Candidate inventory found; no safe inventory artifact, encrypted import, per-account cutover or purge proof exists. |
| AS-09 DLP/redaction/canary/incident response          | partial         | Code/tests exist; legacy HAR/temp/backup estate is not remediated and production incident flow is unproven.         |
| AS-10 customer/legal authorization                    | external-gate   | Code fields exist; owner/legal confirmation for live accounts is unavailable.                                       |
| legacy idempotent migration                           | missing         | No source-to-target mapping, watermark, resumable migrator or counts/hash report found.                             |
| old/V2 shadow reconciliation                          | missing         | Tool directories are empty; no task/answer/eligibility/citation/KPI/report/evidence JSON+Markdown diff exists.      |
| production visual acceptance                          | missing         | Local versioned baselines exist; production screenshots do not.                                                     |
| production deploy/backup/fault drill                  | missing         | No independent production V2 topology, migration or certification evidence exists.                                  |

## Duplicate logic, drift, mocks and untested paths

- S02 router implementation existed but was absent from shared `main.py`; S04 mounted it and regenerated the
  69-path OpenAPI/client.
- Customer, Report Studio and Intelligence still hold product fixtures; these are production blockers.
- Operations execution uses handwritten route strings and local response types instead of the generated client.
- `api/geo_platform/mock.py`, health `mock-ready`, fixture collection screenshot references and fixture probe
  results remain; every runtime occurrence must be classified as test-only or removed from production paths.
- Old and V2 analytics/scoring coexist. S02 has a scoped legacy comparison, but full shadow input/output
  reconciliation is missing.
- No substantive migration, shadow-run, reconciliation, production-deploy, observability or recovery tooling was
  found under the planned V2 tool/deploy boundaries.

## Safe legacy-sensitive inventory baseline

The initial metadata-only scan found 157 HAR candidates, 219 sensitive-name candidate files and approximately
8.53 GB under the workspace, predominantly old work output. No secret values were printed or inspected. The
next required evidence is a machine-readable inventory containing only classified path identifiers/digests,
owner/platform resolution state, size, mode, mtime and content hash, followed by quarantine/import/purge decisions.

## §18 unified completion gate

No §17 item is currently complete under the unified definition. In particular, every item lacks at least the
production deployment, real-role operation, production evidence and status-ledger requirements. Many also lack
real dependency, partial-failure, migration or security evidence. The persistent goal must remain active.
