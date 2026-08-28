#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

compose=(docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml)
postgres_base="postgresql+psycopg://geo:geo_dev_only@127.0.0.1:55433"

create_database() {
  local database_name="$1"
  case "$database_name" in
    geo_platform_s01_ci|geo_platform_knowledge_ci|geo_platform_quota_s07_ci) ;;
    *)
      echo "refusing unexpected CI database name: $database_name" >&2
      return 2
      ;;
  esac

  local exists
  exists="$("${compose[@]}" exec -T postgres psql -U geo -d postgres -Atc \
    "SELECT 1 FROM pg_database WHERE datname='$database_name'")"
  if [[ "$exists" != "1" ]]; then
    "${compose[@]}" exec -T postgres createdb -U geo "$database_name"
  fi
}

create_database geo_platform_s01_ci
create_database geo_platform_knowledge_ci
create_database geo_platform_quota_s07_ci

GEO_POSTGRES_DSN="$postgres_base/geo_platform_s01_ci" \
  .venv/bin/alembic upgrade head
GEO_POSTGRES_DSN="$postgres_base/geo_platform_knowledge_ci" \
  .venv/bin/alembic upgrade head
GEO_POSTGRES_DSN="$postgres_base/geo_platform_quota_s07_ci" \
  .venv/bin/alembic upgrade s07_0002_execution_governance
