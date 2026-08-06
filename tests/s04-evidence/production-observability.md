# Production observability verification

Result: **passed** at 2026-07-25 16:55 CST.

- OTel Collector, Prometheus, Alertmanager, Loki, Alloy and Grafana are running on loopback-only production
  endpoints; the business-metrics exporter and allowlist-only alert receiver are active hardened systemd units.
- Prometheus reports all four configured scrape targets healthy and has GEO API request-count and
  duration series.
- All 12 business rules are loaded. A real collection-analysis admission alert traversed Prometheus,
  Alertmanager and the local safe receiver, and its projected notification is queryable in Loki. The migrated
  lineage defect that triggered it was repaired from V2 rebuilt truth; the current admission backlog and firing
  alert count are both zero.
- Alloy delivers the systemd journal to Loki; the verification query returned two streams and five
  bounded sample entries.
- Grafana health and authenticated API checks return 200. Dashboard `geo-platform-v2-production` version 2
  includes firing-alert, stale-workflow, expired-lease and analysis-admission panels.
- Trace `f1aff637fc4e5bc9a3263c2578ae9215` contains the production API request, Temporal workflow start/run/
  completion and the `collect_with_adapter` Activity start/run under the same trace ID.

The first production workflow probe exposed a migrated configuration contract defect: legacy snapshots did
not contain V2 `models` or `query_groups`. The migrator now materializes those executable fields, preserves
the source-facing fields for audit, and updates snapshots on an idempotent rerun. The repeated production
request returned 202 and completed through the worker.

No credential or sensitive payload is included in this evidence.
