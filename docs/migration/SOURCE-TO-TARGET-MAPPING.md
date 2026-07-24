# Legacy GEO → Platform V2 source-to-target mapping

Version: 1.0 · 2026-07-24  
Source snapshot: read-only SQLite backup plus content-addressed legacy assets  
Target: V2 PostgreSQL/ClickHouse/MinIO. V2 never writes to the source database.

## Identity and project configuration

| Legacy source            | V2 target                                                            | Rule                                                                                                                          |
| ------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `tenant`                 | `platform.tenant`                                                    | Preserve legacy `pub_id` only if it satisfies the V2 opaque-ID contract; otherwise allocate V2 ID and record `legacy_id_map`. |
| `app_user`               | `platform.app_user`                                                  | Migrate identity metadata only. Never copy legacy password/session material; force current identity/OIDC enrollment.          |
| `membership`             | `platform.membership`                                                | Map legacy owner/admin/member/viewer through an explicit role map; unknown roles fail closed for review.                      |
| `session`                | excluded                                                             | Session tokens are not migrated.                                                                                              |
| `customer`               | `platform.customer`                                                  | Preserve tenant relationship and source hash.                                                                                 |
| `brand`                  | `platform.brand`, `brand_alias`, `brand_asset`                       | Split aliases/domains from JSON; validate/canonicalize; keep customer-confirmed provenance.                                   |
| `competitor`             | `platform.competitor`                                                | Resolve brand/project through the ID map.                                                                                     |
| `monitoring_config`      | `platform.project`, `monitoring_config`, `monitoring_config_version` | One legacy config becomes one project plus immutable version snapshot; preserve timezone/cadence intent.                      |
| `query_item`             | `platform.query_group`, `query_item`                                 | Create a deterministic default group per migrated config, then migrate enabled state/text.                                    |
| `project_change_request` | `platform.change_request`                                            | Preserve state and audit actor mapping; invalid states quarantine.                                                            |
| `client_goal`            | `platform.client_goal`                                               | Preserve source values and rebuild any computed baseline facts in V2.                                                         |

## Collection, answers and evidence

| Legacy source                             | V2 target                                                        | Rule                                                                                                                             |
| ----------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `schedule`, `schedule_tick`, `work_item`  | collection plan/run/task records                                 | Historical execution metadata only; no active schedule is enabled until V2 config review.                                        |
| `answer`                                  | authoritative V2 raw-answer ingestion + ClickHouse `answer_fact` | Append-only. Preserve raw text/provenance and eligibility inputs. Do not copy derived KPI values.                                |
| `citation_fact`, `answer.references_json` | `analytics.citation_fact`, ClickHouse `citation_fact`            | Re-normalize URL/title/host using the V2 normalizer; record approved intentional differences.                                    |
| `answer_analysis`                         | rebuild                                                          | Treat as comparison input only. V2 scorer rebuilds `analytics.answer_analysis` from raw answers and records versions/input hash. |
| `cas_blob`, asset bytes                   | MinIO content-addressed object + `evidence.evidence_asset`       | Verify source SHA-256 before upload, DLP before admission, and target SHA-256 after upload.                                      |
| `evidence_ref`, answer screenshots        | `evidence.evidence_relation`/anchors/snapshots                   | Resolve answer and asset maps; missing bytes fail the migration row.                                                             |
| legacy HAR/captcha artifacts              | quarantine by default                                            | Never bulk-import. Apply DLP/redaction; only authorized, necessary, secret-free evidence may enter V2.                           |

## Reports, claims and posting

| Legacy source                                                | V2 target                                 | Rule                                                                                                        |
| ------------------------------------------------------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `publication`, `publication_review`, `customer_confirmation` | `reporting.report*`, reviews and delivery | Import source artifacts after hash verification; rebuild metric/fact snapshots from V2 facts.               |
| legacy report files                                          | MinIO report artifacts                    | Hash, MIME/container validate and bind to an immutable report version.                                      |
| `claim`, `claim_evidence`, `claim_conflict`                  | intelligence claim/evidence/graph records | Preserve raw assertions/evidence links; rebuild source independence, features and scores.                   |
| `post_campaign`, `post_item`, `post_result`                  | no automatic live migration               | Import audit/history only after authorization review. Never infer publish capability or reactivate posting. |
| `otp_event`, `intervention_request`                          | redacted historical event metadata        | Never migrate OTP values, QR payloads, phone values or reusable verification claims.                        |

## Account/profile custody

Legacy `storage_state`, browser profile directories, `.bak`, HAR and temp artifacts use the AS-07 track:

1. Inventory emits path digest, content hash, mode, mtime, size and unresolved owner/platform state only.
2. Confirm tenant, owner, platform, account and authorized scope. Unresolved items stay quarantined.
3. Convert in an isolated runner; never log Cookie/storage/token values.
4. Encrypt with a per-account DEK and AAD-bound tenant/owner/platform/account/profile version.
5. Run L0 integrity, L1 egress, L2 account identity and L3 scoped capability probes.
6. During shadow, legacy is the sole profile writer. No dual-write is permitted.
7. Cut over the fenced lease authority one account at a time.
8. After reconciliation, cryptographically delete and remove old plaintext, indexes and recoverable backups;
   retain only a secret-free deletion receipt.

Device binding, passkey, face/liveness material and device private keys never leave the customer terminal. Only
authorized task capability and non-secret metadata may migrate.

## Idempotency, watermark and recovery

- `integration.migration_run` uniquely identifies a source snapshot hash.
- `integration.legacy_id_map` makes each source entity/PK deterministic and rejects source-hash drift.
- `integration.migration_watermark` stores the last committed source key and counts after each transaction batch.
- A repeated identical snapshot reuses mappings and produces zero duplicate target rows.
- Source drift under an existing key fails closed; it requires a new snapshot/run and reconciliation.
- Derived analytics, report facts, intelligence scores and aggregates are rebuilt after raw import, never trusted.
