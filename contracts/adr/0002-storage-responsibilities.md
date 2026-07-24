# ADR-0002: Storage responsibilities

Status: Accepted · 2026-07-24

PostgreSQL is authoritative for identity, tenancy, business state, evidence metadata, audit, outbox and workflow indexes. ClickHouse contains rebuildable analytical facts/events. MinIO/S3 stores immutable content-addressed objects. Redis is only cache, rate limit and ephemeral notification state. Temporal persists orchestration, not business truth. Cross-store publication uses a PostgreSQL outbox and idempotent consumers.
