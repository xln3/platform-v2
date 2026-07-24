# Parallel development ownership

S00 owns root tooling, contracts, ADRs, CI, Compose, generated-client plumbing and shared boundaries.

S01 exclusively owns `api/geo_platform/{identity,tenancy,projects,collection}`, collection definitions/activities/browser workers, `domain/collection`, and `apps/operations-web/app/features/execution`.

S02 exclusively owns `api/geo_platform/{analytics,evidence,reports,intelligence}`, corresponding domain and workflow files, analytical/evidence/report/investigation database models, ClickHouse, MinIO and pgvector implementations.

S03 exclusively owns all four application product surfaces (except the S01 execution feature) plus `packages/{design-system,charts,evidence-viewer,workflow-ui}` and frontend/visual tests.

Shared `api-client`, `auth`, `domain-types`, OpenAPI, root config or migration policy changes require a `docs/contract-gaps/` proposal. S04 resolves remaining gaps. No session edits another session’s owned file without recording a negotiated handoff.
