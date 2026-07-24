# ADR-0003: Temporal responsibility

Status: Accepted · 2026-07-24

Temporal owns long-running orchestration, retry, timeout, pause/signal, cancellation and recovery. Workflow code is deterministic; activities perform I/O and use business idempotency keys. Final state remains queryable in PostgreSQL-backed APIs. Workflow IDs follow `{workflow-type}/{tenant_pub_id}/{aggregate_pub_id}/{operation_pub_id}` and are stable across retries; run IDs are execution-specific.
