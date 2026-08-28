#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml)
"${compose[@]}" up -d --wait
mkdir -p test-results
"${compose[@]}" ps --format json > test-results/compose-smoke.json
curl --noproxy '*' -fsS http://127.0.0.1:18123/ping | grep -q Ok
"${compose[@]}" exec -T postgres pg_isready -U geo -d geo_platform
"${compose[@]}" exec -T postgres psql -U geo -d geo_platform -Atc \
  "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'" |
  grep -Eq '^[0-9]+(\.[0-9]+)+$'
"${compose[@]}" exec -T redis redis-cli ping | grep -q PONG
"${compose[@]}" exec -T temporal temporal operator cluster health --address temporal:7233
curl --noproxy '*' -fsS http://127.0.0.1:19000/minio/health/live
curl --noproxy '*' -fsS http://127.0.0.1:18080 >/dev/null
