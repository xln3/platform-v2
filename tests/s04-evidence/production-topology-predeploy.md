# S04 production topology — pre-deployment

Captured at 2026-07-25 00:50 CST.

- Public legacy entrypoint: `https://39.105.175.14:8443`.
- Nginx sends the existing legacy pages and `/api/` routes to
  `geosys.service` on `127.0.0.1:8010`.
- The legacy service was active. No legacy route had been changed, redirected,
  overwritten, or removed.
- Seven V2 integration containers were healthy, but the integration compose is
  not production eligible because it embeds development-only credentials.
- No V2 API/worker systemd unit or `/platform/*` and `/api/v2/*` Nginx route was
  installed at capture time.

## Pre-deployment backup

The restricted backup is
`/home/xln/geo-system/backups/s04-production-predeploy-20260725T0050CST`.
It has mode `0700`; copied configuration files have mode `0600`.

- SQLite online backup integrity: `ok`
- Backup files: 10
- Backup bytes: 8,795,401
- CAS entries inventoried: 738
- Sensitive profile entries inventoried without contents: 13,063
- SHA-256 manifest: `SHA256SUMS` inside the restricted backup

No secret value was emitted into this report or command output.
