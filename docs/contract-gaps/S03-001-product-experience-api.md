# S03-001 — Product experience API and safe account projections

Status: customer account contract integrated; S02 product contracts remain  
Owner: S03  
Consumers: Customer Web, Operations Web, Report Studio, Intelligence Web

## Gap

The generated OpenAPI now exposes 44 S01 paths for identity, customers, projects/resources/config freeze,
collection runs, platform accounts/authorizations/profiles/leases/health, interventions, events, revocation,
quarantine and break-glass. `pnpm check:api` passes and S03 applications consume the shared
`openapi-fetch<paths>` client for available shared calls.

The current generated schema still contains zero S02 analytics, evidence, exports, reports or intelligence paths.
Customer/Report/Intelligence therefore retain visibly labelled contract fixtures for those product records.

S01 has added the narrow customer-safe authorization/health/intervention/event/revocation contract. Customer Web
now consumes it through generated path types and has proved role/DLP behavior against the integrated API.

## Required safe account projection

Customer and Operations product surfaces require an S01-owned projection containing only account mask, platform
label, owner label, custody mode, admission level, granted scopes, expiry label/time, region label, session health,
last verified time, intervention status, event summary and revocation receipt metadata.

Responses must reject or strip unknown properties and must never include Cookie, Authorization, access/refresh
token, OTP, QR payload, proxy password, full phone/email, browser-profile path, storage state, device private key,
HAR secrets or biometric material. Unauthorized roles receive the same forbidden/not-found envelope and cannot
infer account existence.

## Required endpoint families

- customer member administration (customer browser bootstrap and customer-safe account lifecycle are integrated);
- analytics KPI cells, trends, models, regions, modes, competitors, question rows and contribution traces;
- answers, citations, answer/source screenshots, anchors, history diffs, evidence packages and grants;
- reports, frozen windows, sections, charts, comments, reviews, versions, previews, publication and confirmation;
- investigations, claims, evidence matrix, independence clusters, propagation graph/table, rules, verdict and appeal;
- a customer registration/authorization update that can assign a responsible person distinct from the authenticated
  actor. The current endpoint binds both owner and responsible person to that actor.

## Current integration boundary

- Real and generated: identity/project bootstrap, health, S01 Operations execution/account lifecycle, Customer
  account registration/authorization/pairing/events/revocation, and Customer project `change-requests`. Live
  account responses pass through an allow-list projection before React state; pairing transitions remain owned by
  the controlled terminal, and revocation remains pending until a real receipt exists. Change requests are selected
  only after a validated live identity; contract-fixture sessions
  keep their writes explicitly local. Validated request headers are memory-only, never persisted into Query cache,
  URL or telemetry, and the browser never receives a service token.
- The mounted project catalog exposes brands, competitors, goals and change requests as separate writes, but the
  existing Customer “brand + product/service + competitor + prohibited claim” confirmation is one logical
  transaction. There is no product/service resource kind or atomic composite request, so S03 does not split that
  confirmation into partially successful live mutations. A composite/versioned customer-confirmation contract is
  required before replacing that fixture workflow.
- Contract fixture: Customer analytics/answers/evidence/reports.
- Contract fixture: Report Studio and Intelligence business records.
- Not present in the mounted/frozen OpenAPI: S02 analytics/evidence/reports/intelligence routers.
- Remaining frontend contract drift outside S03 ownership: S01's
  `features/execution/account-management` API module declares local response types; S03 will not edit or duplicate
  that owner-controlled business module.

All list filters use URL-bound cursor pagination; every state distinguishes loading/empty/real-zero/insufficient/
failed/delayed/forbidden/ready. Writes use `Idempotency-Key`.

## Canvas ADR proposal

S03 selects **Konva / react-konva** for screenshot anchor annotation because the required interaction is a bounded
canvas overlay (rectangles, handles, zoom and coordinates), not a general design editor. Its smaller conceptual
surface and direct scene graph fit evidence annotation. Fabric.js is not selected. Because `contracts/adr` is
S00-owned and accepted ADRs are immutable, S03 records the complete proposed decision in
`docs/contract-gaps/S03-ADR-0005-konva-evidence-annotation.md` for the next shared ADR number instead of silently
editing the shared directory.
