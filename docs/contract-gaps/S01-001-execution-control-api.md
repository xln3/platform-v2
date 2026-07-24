# S01-001 — Execution control API expansion

Status: implemented and closed  
Owner: S01  
Date: 2026-07-24

## Gap

The S00 contract exposes only `GET /api/v2/projects` and `POST /api/v2/collection/runs`.
S01 requires the identity, membership, project/configuration, execution-control, platform-account,
profile, lease, intervention, pairing, revocation, and event APIs assigned to it.

## Stable contract

- Preserve `/api/v2/*`, opaque public IDs, structured `ApiError`, cursor pages, tenant guards,
  `Idempotency-Key`, stable workflow IDs, and the existing `listProjects` /
  `startCollectionRun` operation IDs.
- Add explicit actor/session headers behind a replaceable session/OIDC adapter for development.
- Customer projections never include platform accounts, profiles, proxies, device fingerprints,
  challenges, internal prompts, or secret-bearing fields.
- Operations projections expose masked account and non-secret health/provenance only.
- Long-running controls return persisted workflow/run identifiers; APIs never mutate a workflow
  terminal state directly.
- No profile download endpoint will be introduced.

## Consumer impact

OpenAPI and the generated TypeScript client gain additive operations and schemas. Existing S00
consumers remain source-compatible. S03 can use account-free customer projections and contract
fixtures without reading profile or secret fields.

## Negotiated integration handoff

S03 established the Operations shell while explicitly reserving execution business files for S01.
S01 adds only the `/platform/operations/execution` route registration to the S03-owned route table;
the route implementation, API integration, state handling, and account-management page remain
inside `features/execution`. The existing S03 index shell and shared design files are unchanged.

## Closure evidence

- The additive OpenAPI contains the authoritative identity, customer/project/config, collection,
  adapter/account/profile, resource/lease, intervention, Break-glass, revocation and event routes.
- Generated client typecheck, S01 PostgreSQL/Temporal tests and Operations desktop/tablet/mobile
  E2E pass.
- Existing S00 routes and operation IDs remain present. No consumer-breaking route replacement
  was introduced.
