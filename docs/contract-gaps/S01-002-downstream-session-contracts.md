# S01-002 — Downstream session and customer-safe account contracts

Status: implemented; S04 mounting handoff remains  
Owner: S01  
Consumers: S02, S03, S04  
Date: 2026-07-24

## Gap

The earlier S01 completion audit missed two S01-owned contracts recorded by downstream sessions:

- S02 authenticated evidence required an authoritative, secret-free capability-lease validation
  operation rather than its local gateway mock.
- S03 Customer Web required a customer-role safe account projection and lifecycle family rather
  than an account fixture or access to Operations/Profile projections.

## Implemented capability contract

- `POST /api/v2/collection/capability-leases`
- `POST /api/v2/collection/capability-leases/{lease_pub_id}/validate`
- `POST /api/v2/collection/capability-leases/{lease_pub_id}/revoke`

The lease is bound to tenant, opaque account ID, allowed hostnames, actions, authorization scopes,
subject workflow, expiry and bounded use count. Validation requires an active scoped Worker service
credential and an active underlying account authorization. Explicit lease revocation and account
revocation both stop subsequent validation. Responses never contain credentials, OTP, Profile
references, device keys, proxy secrets or Vault handles.

## Implemented customer-safe contract

- `GET|POST /api/v2/customer/platform-accounts`
- `POST /api/v2/customer/platform-accounts/{account_pub_id}/authorizations`
- `GET|POST /api/v2/customer/platform-accounts/{account_pub_id}/pairings`
- `GET /api/v2/customer/platform-accounts/{account_pub_id}/events`
- `POST /api/v2/customer/platform-accounts/{account_pub_id}/revoke`

The projection is owner-scoped and allow-listed: mask, platform label, custody, admission, scopes,
authorization expiry, region, health, last verification, intervention state and revocation receipt.
It exposes neither Profile/session internals nor pairing tokens. The customer creates a safe pairing
request; Operations or a controlled terminal establishes and completes the one-time pairing.

## Evidence

- `tests/s01-s02-capability-runtime.json`: real S02 client → running S01 API validation and
  post-revocation rejection.
- `tests/s01-customer-account-runtime.json`: customer authorization → pairing → completion →
  Temporal revocation and safe receipt.
- S01 suite: 31 passed; strict mypy and Ruff pass.
- Running S01 OpenAPI: 44 paths and all required routes above present.

## Remaining handoff

`api/geo_platform/s02_routers.py` is independently implemented but not mounted by the shared
application. Per ownership, S04 must mount that bundle and regenerate the integrated OpenAPI/client.
This is not an S01 router or data-authority change.
