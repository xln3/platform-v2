# ADR-0004: Routing and legacy coexistence

Status: Accepted · 2026-07-24

V2 owns only `/platform/customer/*`, `/platform/operations/*`, `/platform/reports/*`, `/platform/intelligence/*` and `/api/v2/*`. Existing `/client/*`, `/portal`, `/ops`, `/collect`, `/pipeline`, `/api/client/*` and `/api/ops/*` stay unchanged. V2 never redirects, overwrites, mounts under, or writes through an old route. A CI hash guard protects legacy route implementation files.
