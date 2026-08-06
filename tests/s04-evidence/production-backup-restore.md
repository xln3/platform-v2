# Production backup and restore drill

The restricted production backup is stored at
`/home/xln/geo-system/backups/s04-v2-production-certified-20260725T0136CST`.
Its SHA-256 manifest verified successfully before restore.

- PostgreSQL custom-format restore reached schema `s04_0006` and reproduced 1 tenant,
  5 memberships, 5 projects, 146 answers, 146 analyses, 2,754 citations and 533 evidence rows.
- ClickHouse Native restores reproduced 146 answer facts, 2,754 citation facts,
  146 run events, 1,022 metric rows and zero feature rows.
- The MinIO archive extracted successfully to an isolated directory with 959 files and
  248,716,210 restored tree bytes.
- Temporary restore databases and directories were removed after verification.
- The legacy service, database, assets and routes were not modified.

Result: **PASS**
