#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

readonly default_business_database="geo_platform"
readonly docker_context="default"
readonly expected_docker_host="unix:///var/run/docker.sock"
readonly compose_project="geo-platform-v2"
readonly -a allowed_databases=(
  geo_platform_s01_ci
  geo_platform_knowledge_ci
  geo_platform_quota_s07_ci
)

dry_run=0
all_requested=0
declare -a requested_databases=()

usage() {
  cat <<'EOF'
Usage: bash scripts/cleanup_ci_test_databases.sh [--dry-run] (--all | DATABASE ...)

Use --all to clean all three repository-owned CI test databases. With no --all,
at least one explicit DATABASE is required.
Only these database names are accepted:
  geo_platform_s01_ci
  geo_platform_knowledge_ci
  geo_platform_quota_s07_ci
EOF
}

is_allowed_database() {
  local candidate="$1"
  local allowed
  for allowed in "${allowed_databases[@]}"; do
    if [[ "$candidate" == "$allowed" ]]; then
      return 0
    fi
  done
  return 1
}

append_database_once() {
  local candidate="$1"
  local existing
  for existing in "${requested_databases[@]}"; do
    if [[ "$candidate" == "$existing" ]]; then
      return
    fi
  done
  requested_databases+=("$candidate")
}

while (($# > 0)); do
  case "$1" in
    --dry-run)
      dry_run=1
      ;;
    --all)
      all_requested=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      while (($# > 0)); do
        append_database_once "$1"
        shift
      done
      break
      ;;
    -*)
      echo "refusing unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      append_database_once "$1"
      ;;
  esac
  shift
done

if ((all_requested)) && ((${#requested_databases[@]} > 0)); then
  echo "refusing --all together with explicit database targets" >&2
  exit 2
fi

if ((all_requested)); then
  requested_databases=("${allowed_databases[@]}")
fi

if ((${#requested_databases[@]} == 0)); then
  echo "refusing cleanup without --all or an explicit CI test database" >&2
  usage >&2
  exit 2
fi

# Validate the complete request before issuing any database command. A mixed
# valid/invalid argument list therefore cannot partially clean the environment.
for database_name in "${requested_databases[@]}"; do
  if [[ "$database_name" == "$default_business_database" ]]; then
    echo "refusing default business database: $database_name" >&2
    exit 2
  fi
  if ! is_allowed_database "$database_name"; then
    echo "refusing unknown CI test database: $database_name" >&2
    exit 2
  fi
done

if ((dry_run)); then
  for database_name in "${requested_databases[@]}"; do
    echo "dry-run terminate-connections database=$database_name"
    echo "dry-run drop-if-exists database=$database_name"
  done
  exit 0
fi

if [[ -n "${DOCKER_HOST:-}" || -n "${DOCKER_CONTEXT:-}" || \
      -n "${DOCKER_CONFIG:-}" || -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  echo "refusing Docker or Compose environment overrides for database cleanup" >&2
  exit 2
fi

actual_docker_host="$(
  docker context inspect "$docker_context" --format '{{.Endpoints.docker.Host}}'
)"
if [[ "$actual_docker_host" != "$expected_docker_host" ]]; then
  echo "refusing non-local Docker context: $actual_docker_host" >&2
  exit 2
fi

compose=(
  docker --context "$docker_context" compose
  --project-name "$compose_project"
  -f compose.yaml
  -f deploy/s02/compose.pgvector.yaml
)
postgres_container_id="$("${compose[@]}" ps -q postgres)"
if [[ -z "$postgres_container_id" || "$postgres_container_id" == *$'\n'* ]]; then
  echo "refusing missing or ambiguous geo-platform-v2 postgres container" >&2
  exit 2
fi
container_identity="$(
  docker --context "$docker_context" inspect \
    --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}' \
    "$postgres_container_id"
)"
if [[ "$container_identity" != "$compose_project|postgres" ]]; then
  echo "refusing unexpected postgres container identity: $container_identity" >&2
  exit 2
fi

for database_name in "${requested_databases[@]}"; do
  echo "terminating CI test database connections: $database_name"
  "${compose[@]}" exec -T postgres \
    psql -U geo -d postgres \
    --set ON_ERROR_STOP=1 \
    --command \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database_name' AND pid <> pg_backend_pid();"

  echo "dropping CI test database if present: $database_name"
  "${compose[@]}" exec -T postgres \
    dropdb -U geo --if-exists --force "$database_name"
done
