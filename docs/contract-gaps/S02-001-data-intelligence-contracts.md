# S02-001 — Data intelligence APIs and runtime dependencies

Status: resolved for S02; frontend/production certification belongs to S04  
Owner: S02  
Date: 2026-07-24

## Gap

The frozen S00 OpenAPI exposes no analytics, evidence, reports, exports or intelligence
operations. The Python runtime also has no S3/document-generation client, and the PostgreSQL
image does not ship the `vector` extension.

## Stable contract

- Add `/api/v2/{analytics,evidence,reports,exports,intelligence}/*` operations without changing
  existing operation IDs or response shapes.
- Preserve opaque public IDs, cursor pages, structured errors, tenant context and idempotency.
- KPI responses always disclose value, numerator, denominator, state, versions, filter hash and
  a trace token.
- Customer aggregates exclude account-level dimensions by default.
- Evidence/report projections expose only redacted opaque provenance. They never expose cookies,
  tokens, OTP, profile object references, device keys, proxy credentials or verification detail.
- Authenticated capture accepts only a short-lived S01 capability-lease assertion bound to
  account, domain, action, scope and expiry; S02 receives no Vault/profile access.

## Required S01 validation operation

S02's narrow client calls
`POST /api/v2/collection/capability-leases/{lease_pub_id}/validate` with tenant, opaque platform
account ID, hostname, action, required scopes and workflow ID. A successful response must return
only lease public ID, tenant/account opaque IDs, allowed domains/actions/scopes, workflow binding,
expiry and revocation state. It must never return a cookie, token, OTP, device key, proxy secret,
Vault handle or decryptable profile reference.

S01 now owns issuance, revocation and authoritative validation at migration head `s01_0005`.
The endpoint is present in the checked-in and running OpenAPI, and its secret-free response,
binding denials, expiry, revocation and audit behavior pass together with the S02 lease client
tests. S02 does not implement a shadow lease database. S04 recorded a real-endpoint-backed
EvidenceCapture Temporal run, including successful persisted capture and post-revocation
rejection, at `tests/s04-evidence/real-gateway-evidence-runtime.json`.

## Shared runtime changes requested

- Add an S3-compatible client and DOCX/PDF/XLSX generation libraries to the Python dependency
  contract, or accept the S02 standard-library implementations.
- Adopt the tested `deploy/s02/compose.pgvector.yaml` override (pgvector PostgreSQL 16), or change
  the shared development image. Native pgvector+FTS is now the authoritative S02 implementation.
- S04 mounted the additive router bundle in the shared FastAPI application; the current generated
  OpenAPI/client has 71 paths, including all 27 S02 paths. Full frontend and production
  certification remain S04 work and do not block the S02 completion boundary.

## Backward compatibility

All changes are additive. Existing S00 and S01 consumers remain source-compatible. S02 keeps its
router bundle independently testable while the shared application is now authoritative.
