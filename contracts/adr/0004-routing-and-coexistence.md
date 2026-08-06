# ADR-0004: One authoritative production runtime

Status: Accepted · 2026-08-03

GEO Platform V2 is the only public and operational production runtime. It owns
`/`, `/platform/customer/*`, `/platform/operations/*`,
`/platform/reports/*`, `/platform/intelligence/*`, and `/api/v2/*`.

The root redirects to the V2 customer workspace. All other application and API
paths fail closed. Production ingress must not proxy to port 8010 or expose OTP
relay and remote-desktop surfaces. The superseded application may exist only in
an access-controlled, offline recovery backup; it is not a dependency,
fallback, user-visible option, or deployment target.

`scripts/check_production_routes.py` and
`contracts/production-route-manifest.json` enforce this boundary in CI.
