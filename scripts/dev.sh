#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml)
"${compose[@]}" up -d --wait
.venv/bin/alembic upgrade head
trap 'jobs -p | xargs -r kill' EXIT INT TERM
PYTHONPATH=api:. .venv/bin/uvicorn geo_platform.main:app --host 127.0.0.1 --port 45200 &
PYTHONPATH=api:. .venv/bin/python -m workflows.workers.main &
PYTHONPATH=api:. .venv/bin/python -m workflows.workers.source &
PYTHONPATH=api:. .venv/bin/python -m workflows.workers.analysis &
corepack pnpm dev:web &
wait
