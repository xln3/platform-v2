#!/usr/bin/env bash
set -euo pipefail

# Conservative, unattended G07-G34 top-up cadence for spare-capacity windows.
# One hourly invocation considers one rotating leg and launches at most four
# questions.  Every guard is fail-closed so report production and foreground
# collections take priority.

ROOT=/home/xln/geo-system/platform-v2
PYTHON="$ROOT/.venv/bin/python"
PSQL=/usr/bin/psql
TOKEN_FILE=/tmp/s04-acceptance-token
LOCK_FILE=/tmp/sbaq-g0734-gradual.lock
LOG_PREFIX=sbaq_g0734_gradual
ZOMBIE_RUN=run_3J895WRN5MGF6CQFXVZ370MR50
MIN_AVAILABLE_KB=8388608
MAX_LOAD_ONE=8

log() {
  printf '%s %s %s\n' "$(date '+%F %T %Z')" "$LOG_PREFIX" "$*"
}

exec 9>"$LOCK_FILE"
if ! /usr/bin/flock -n 9; then
  log "skip reason=lock_busy"
  exit 0
fi

if [[ -e "$TOKEN_FILE" ]]; then
  log "skip reason=acceptance_token_in_use"
  exit 0
fi

available_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
load_one=$(awk '{print $1}' /proc/loadavg)
if (( available_kb < MIN_AVAILABLE_KB )); then
  log "skip reason=memory available_kb=$available_kb threshold_kb=$MIN_AVAILABLE_KB"
  exit 0
fi
if ! awk -v current="$load_one" -v maximum="$MAX_LOAD_ONE" 'BEGIN {exit !(current < maximum)}'; then
  log "skip reason=load load_one=$load_one threshold=$MAX_LOAD_ONE"
  exit 0
fi

dsn_raw=$(/usr/bin/sudo -n /usr/bin/sed -n 's/^GEO_POSTGRES_DSN=//p' /etc/geo-platform-v2/platform.env | /usr/bin/head -1)
if [[ -z "$dsn_raw" ]]; then
  log "skip reason=missing_dsn"
  exit 0
fi
psql_dsn=${dsn_raw/postgresql+psycopg:\/\//postgresql:\/\/}

active_runs=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT count(*) FROM platform.collection_run WHERE state IN ('pending','running','paused') AND pub_id <> '$ZOMBIE_RUN'"
)
if (( active_runs > 0 )); then
  log "skip reason=active_collection count=$active_runs"
  exit 0
fi

active_reports=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT count(*) FROM reporting.formal_report_production WHERE status NOT IN ('awaiting_review','failed','signed')"
)
if (( active_reports > 0 )); then
  log "skip reason=active_report count=$active_reports"
  exit 0
fi

legs=(deepseek-sh yiyan-bj yiyan-sh)
epoch_hour=$(( $(date +%s) / 3600 ))
leg=${legs[$(( epoch_hour % ${#legs[@]} ))]}
browser_unit="geo-platform-v2-browser@${leg//-/_}.service"
if [[ "$(systemctl is-active "$browser_unit")" != active ]]; then
  log "skip reason=browser_inactive leg=$leg unit=$browser_unit"
  exit 0
fi
if [[ "$(systemctl is-active geo-platform-v2-worker.service)" != active ]]; then
  log "skip reason=worker_inactive"
  exit 0
fi

recent_failures=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT count(*) FROM platform.collection_run WHERE idempotency_key LIKE 'sbaq-g0734-gradual-$leg-%-run' AND updated_at >= now() - interval '12 hours' AND (state IN ('failed','cancelled','completed_with_failures') OR failed_tasks > 0)"
)
if (( recent_failures > 0 )); then
  log "skip reason=leg_cooldown leg=$leg recent_failures=$recent_failures"
  exit 0
fi

plan=$(GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$ROOT/tools/topup_sbaq_g0734_20260814.py" --leg "$leg" --max-queries 4)
launch_count=$("$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["launch_query_count"])' <<<"$plan")
if (( launch_count == 0 )); then
  log "skip reason=leg_complete leg=$leg"
  exit 0
fi

cleanup_token() {
  if [[ ! -f "$TOKEN_FILE" ]]; then
    return
  fi
  "$PYTHON" -c 'import httpx, pathlib
p = pathlib.Path("/tmp/s04-acceptance-token")
token = p.read_text().strip()
try:
    httpx.post(
        "https://127.0.0.1:8443/api/v2/identity/logout",
        verify=False,
        trust_env=False,
        cookies={"__Host-geo_session": token},
        timeout=30,
    ).raise_for_status()
except Exception:
    pass
' || true
  /usr/bin/unlink "$TOKEN_FILE" || true
}
trap cleanup_token EXIT

GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$ROOT/tools/mint_acceptance_session.py" >/dev/null
pass_id="auto-$(date '+%Y%m%dT%H')"
output=$(
  GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$ROOT/tools/topup_sbaq_g0734_20260814.py" \
    --leg "$leg" \
    --pass-id "$pass_id" \
    --max-queries 4 \
    --launch
)
log "launch leg=$leg payload=$output"
