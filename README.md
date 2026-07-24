# GEO Platform V2 — S00 foundation

This directory is independent from the production Flask application. It owns only `/platform/*` and `/api/v2/*`; old routes remain untouched.

## Bootstrap

```bash
make install
make check
make dev
```

`make dev` is the single foreground command: it starts PostgreSQL, ClickHouse, Temporal/UI, MinIO and Redis, then the API, Temporal worker and all four web shells. Stop it with Ctrl-C; dependency containers remain available for fast restart. Use `make infra-down` when they should stop.

See `contracts/CONVENTIONS.md`, `contracts/adr/`, `docs/OWNERSHIP.md`, and `docs/session-status/S00.md` before parallel development.
