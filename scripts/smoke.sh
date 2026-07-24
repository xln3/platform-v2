#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --wait
docker compose ps --format json > tests/compose-smoke.json
curl --noproxy '*' -fsS http://127.0.0.1:18123/ping | grep -q Ok
docker compose exec -T postgres pg_isready -U geo -d geo_platform
docker compose exec -T redis redis-cli ping | grep -q PONG
docker compose exec -T temporal temporal operator cluster health --address temporal:7233
curl --noproxy '*' -fsS http://127.0.0.1:19000/minio/health/live
curl --noproxy '*' -fsS http://127.0.0.1:18080 >/dev/null
