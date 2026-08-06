# Production workflow resilience verification

Result: **passed** against the isolated production Temporal service.

- A controlled Activity failed on attempt one and completed on retry.
- A 15-second Activity emitted one-second heartbeats and completed normally.
- `geo-platform-v2-worker` was stopped while a workflow was durably waiting.
- Duplicate pause Signals, a duplicate intervention nonce and cancellation were accepted while the Worker was
  stopped.
- After the systemd Worker restarted, history replay applied cancellation and completed the workflow as
  `cancelled`.
- The Worker was active after certification and no certification workflow remained running.

The probe writes bounded Temporal test histories only and does not mutate customer database rows.
