#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --wait
trap 'jobs -p | xargs -r kill' EXIT INT TERM
PYTHONPATH=api:. .venv/bin/uvicorn geo_platform.main:app --host 127.0.0.1 --port 45200 &
PYTHONPATH=api:. .venv/bin/python -m workflows.workers.main &
corepack pnpm dev:web &
wait
