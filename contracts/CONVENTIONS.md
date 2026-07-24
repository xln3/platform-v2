# Frozen cross-session conventions

## Identity

Database `id` is an internal `BIGINT` and never crosses an API. `pub_id` is `<type>_<ULID>` (uppercase Crockford ULID, opaque and non-enumerable). Known prefixes include `tnt`, `usr`, `prj`, `run`, `ans`, `evd`, `rpt`, `inv`, `evt`. References use explicit names such as `project_pub_id`.

## Time

Persistence and API timestamps are UTC, RFC 3339 with `Z` and microsecond precision where available. Local calendar intent stores an IANA timezone separately. UI alone formats local time. DST calculations use the IANA zone, never a fixed offset.

## Errors

Non-2xx JSON is `{ "error": { "code", "message", "request_id", "details" } }`. `code` is stable English machine text; `message` is informational and must not be parsed. Validation details identify fields without secrets.

## Pagination

All lists use opaque cursor pagination: request `cursor` and `limit` (default 50, max 100); response `{data, page:{next_cursor,has_more}}`. Cursors bind ordering and normalized filters and must not expose database IDs.

## Idempotency

Every externally initiated write accepts `Idempotency-Key` (16–128 printable ASCII). Scope is tenant + operation + key. Same key/body replays the original status/body; same key/different body returns `409 idempotency_conflict`. Retention is at least 24 hours.

## Tenant context and RBAC

Authenticated identity comes from the server-side session/OIDC token. `X-Tenant-Id` selects one membership and never grants access. Every repository method requires tenant context. Roles are `customer`, `operator`, `analyst`, `reviewer`, `admin`; permissions are explicit `resource:action` capabilities, optionally project-scoped. Customer responses exclude accounts, proxies, device fingerprints, captcha/OTP, internal prompts and raw risk data.

## Audit and outbox

Audit events are append-only and named `<domain>.<resource>.<past-tense-action>`. They contain event/tenant/actor/resource IDs, UTC occurrence time, request/trace IDs and redacted data. Outbox envelope version `1.0` contains event ID/type/time, tenant, aggregate, trace and payload. Consumers deduplicate by event ID.

## Workflow IDs

`{workflow-type}/{tenant_pub_id}/{aggregate_pub_id}/{operation_pub_id}`. Workflow type is kebab-case. Retry reuses the workflow ID; a deliberate new operation gets a new operation pub ID. API returns workflow ID immediately with HTTP 202.
