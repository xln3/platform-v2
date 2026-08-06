# S02 completion audit

Audited: 2026-07-25 01:15 (Asia/Shanghai)  
Rule: PASS requires executable current-state evidence; schema or intent alone is insufficient.

## Data and storage

| Requirement                                                                     | Status | Authoritative evidence                                                                                                                                |
| ------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL analytics/evidence/report/investigation graph                        | PASS   | `s02_0001`–`s02_0008`; integrated PostgreSQL has 49 S02 tables                                                                                        |
| ClickHouse answer/citation/run/feature/aggregate facts                          | PASS   | `deploy/clickhouse/001_s02_analytics.sql`; five real `geo_analytics` tables; store/projection tests                                                   |
| MinIO SHA-256 CAS and short authorization                                       | PASS   | `evidence/object_store.py`; real hash/tamper/presign tests                                                                                            |
| Native pgvector + FTS hybrid retrieval                                          | PASS   | vector 0.8.5, `vector(384)`, HNSW index; real hybrid-search vertical test                                                                             |
| Transactional outbox/idempotent consumption/replay                              | PASS   | `analytics/outbox.py`; duplicate receipt and ClickHouse projection tests                                                                              |
| Object success/DB failure, duplicate, tamper, recovery                          | PASS   | `test_s02_evidence_service.py`, `test_s02_stores.py`; CAS-first orphan recovery                                                                       |
| Redacted opaque provenance for answers/citations/evidence/reports/investigation | PASS   | answer provenance columns; citation joins same answer/run; all screenshots/report/investigation evidence links resolve to `evidence_asset` provenance |
| No credentials/profile secrets in analytical stores                             | PASS   | provenance validation, ClickHouse field rejection and DLP matrix                                                                                      |
| Customer aggregate excludes account dimension                                   | PASS   | service hard rejection and integration assertion                                                                                                      |

## Unified analytics

| Requirement                                                    | Status | Authoritative evidence                                                                                                                  |
| -------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Framework-free eligibility/scoring/aggregation                 | PASS   | `domain/scoring`, `domain/metrics`; strict import/type checks                                                                           |
| Answer/citation facts, registry, KpiCell, daily metrics        | PASS   | migrations/services and real aggregation tests                                                                                          |
| Mention/rank/TopN/sentiment/recommendation/competitor/citation | PASS   | analyzer/registry plus Chinese-rank and competitor aggregate tests                                                                      |
| Question/model/region/mode/time/competitor filtering           | PASS   | arbitrary controlled dimensions; API fields; competitor aggregation test                                                                |
| Previous delta/trend/trace/anomaly root cause                  | PASS   | analytics service/domain and unit/API tests                                                                                             |
| Uncalibrated recommendation explicitly experimental            | PASS   | KpiCell state/advisory and Temporal/domain assertions                                                                                   |
| Pages/exports/reports share aggregation kernel                 | PASS   | analytics API and `ExportService` call `AnalyticsService.aggregate`; report facts are frozen from the same result in the vertical slice |
| Legacy algorithm reconciliation with explained differences     | PASS   | `test_s02_legacy_reconciliation.py` documents exact and intentional rank differences                                                    |

## Evidence

| Requirement                                                           | Status             | Authoritative evidence                                                                                                   |
| --------------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| EvidenceCaptureWorkflow                                               | PASS               | real Temporal lease → DLP → MinIO → PostgreSQL test                                                                      |
| Answer/source screenshot, HTML/PDF/text snapshots                     | PASS               | evidence kind-agnostic capture plus real binary/text/container artifact tests                                            |
| OCR interface and text/bbox anchors                                   | PASS               | `persist_ocr`, span validation and real PostgreSQL bbox test                                                             |
| Answer/citation/source/screenshot linking                             | PASS               | evidence relation and claim-evidence graph vertical tests                                                                |
| Numbered history, text and visual diff                                | PASS               | snapshot sequence and perceptual/text diff integration test                                                              |
| Evidence center API/package/share/revoke/expiry/audit                 | PASS in S02 router | isolated API plus real package access lifecycle tests                                                                    |
| Published-report evidence retention                                   | PASS               | `s02_0008` reference FKs and trigger tests for both artifacts and cited screenshots                                      |
| Public/private/paid access classes and authorized-session provenance  | PASS               | package/public-conclusion filtering and workflow provenance assertions                                                   |
| DLP before screenshot/HTML/HAR/OCR/exception/export/package admission | PASS               | DLP matrix, CAS boundary and binary fail-closed Temporal test                                                            |
| Login capture only through scoped S01 lease                           | PASS               | current real-gateway EvidenceCapture persisted after S01 validation and rejected capture after explicit lease revocation |

## Reports and exports

| Requirement                                                | Status | Authoritative evidence                                                                        |
| ---------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------- |
| ReportProductionWorkflow                                   | PASS   | real Temporal production, four artifacts, Signal review and database publication              |
| Frozen window/filter/metric/scorer/fact versions           | PASS   | freeze domain, persisted hashes and drift tests                                               |
| KPI/chart/section/evidence/recommendation components       | PASS   | typed component schema; report safety/rendering and vertical production                       |
| AI draft and human edit audit                              | PASS   | version hashes and `human_edited` event                                                       |
| DOCX/PDF/XLSX/online HTML                                  | PASS   | real container validation and MinIO artifacts                                                 |
| Version diff/comments/review/publish/customer confirmation | PASS   | two database versions, diff, comments, review gate, delivery test                             |
| Optimization owner/status/outcome review                   | PASS   | service and vertical test                                                                     |
| Interruption and human Signal recovery                     | PASS   | real Temporal worker stop/restart/replay test                                                 |
| Customer output hides account/profile/verification details | PASS   | report policy rejects forbidden fields; artifacts contain only supplied safe business content |
| Independent frozen exports                                 | PASS   | `reporting.data_export`, exports API and real XLSX evidence test                              |

## Anti-GEO

| Requirement                                                              | Status | Authoritative evidence                                                           |
| ------------------------------------------------------------------------ | ------ | -------------------------------------------------------------------------------- |
| Content/version/author/domain/entity/claim/occurrence/evidence graph     | PASS   | migrations, source/entity services and raw-post vertical test                    |
| supports/contradicts/insufficient/derived_from/near_duplicate            | PASS   | graph/link services and vertical assertions                                      |
| Multi-source search/canonical dedup/hash/semantic/same-source clustering | PASS   | native hybrid search, body-hash dedup and similarity/source-independence records |
| Content/source/propagation/external-fact features                        | PASS   | typed feature persistence for all four families                                  |
| Source independence/circular citation/propagation timeline               | PASS   | source assessment, propagation event and scoring inputs                          |
| AntiGeoInvestigationWorkflow                                             | PASS   | real Temporal persisted score and human verdict                                  |
| Probability/sufficiency/source count/uncertainty/model+rule versions     | PASS   | detection score and public conclusion                                            |
| Human verdict/review/appeal/correction                                   | PASS   | superseding verdict and appeal vertical test                                     |
| Never infer definite GEO from one post                                   | PASS   | probability cap ≤0.49, disclaimer and human-verdict requirement                  |
| Cluster-split evaluation and metrics                                     | PASS   | leakage guard plus precision/recall/FPR/Brier/explanation completeness tests     |
| Private/paid investigation data excluded publicly                        | PASS   | public conclusion and package tests                                              |

## Reliability and completion gates

| Gate                                                          | Status | Evidence or blocker                                                      |
| ------------------------------------------------------------- | ------ | ------------------------------------------------------------------------ |
| 1. Answer → KPI/trace/screenshot/report runs                  | PASS   | real vertical test and persisted Temporal activities                     |
| 2. Post → claim/multi-source/score/verdict runs               | PASS   | real vertical test and persisted Temporal verdict                        |
| 3. PostgreSQL/ClickHouse/MinIO consistency/recovery           | PASS   | real service integration and injected failure/replay tests               |
| 4. Four workflows interrupt/recover/idempotent                | PASS   | real Temporal tests and durable workflow-operation keys                  |
| 5. Opaque provenance traceable without secrets                | PASS   | DLP/projection/ClickHouse/report/public-conclusion tests                 |
| 6. Login evidence uses real S01 gateway; revoke stops capture | PASS   | current `tests/s02-real-gateway-runtime.json` plus S04 independent proof |
| 7. S02 status contains tests/data/evidence/gaps               | PASS   | `S02.md`, this audit, `s02-verification.json`, contract-gap document     |

## Shared integration resolution

- S01 implements and mounts the authoritative capability validation operation.
- S04 mounts the S02 router bundle and generated additive OpenAPI/client contract; the current
  runtime exposes all 27 S02 paths.
- EvidenceCapture was rerun against the current integrated S01 endpoint: authorized capture
  persisted, revocation rejected the next workflow, and the evidence contains no service token
  or secret.
- All seven S02 gates are complete.
