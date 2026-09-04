#!/usr/bin/env bash
set -euo pipefail

# Conservative, unattended G01-G34 top-up cadence for spare-capacity windows.
# Each invocation considers one rotating leg and launches at most four
# questions.  Up to two gradual runs may overlap on distinct browser legs;
# every guard is fail-closed so report production and foreground collections
# take priority.

ROOT=/home/xln/geo-system/platform-v2
PYTHON="$ROOT/.venv/bin/python"
PSQL=/usr/bin/psql
TOKEN_FILE=/tmp/sbaq-gradual-acceptance-token
LOCK_FILE=/tmp/sbaq-g0734-gradual.lock
MAINTENANCE_FILE=/tmp/sbaq-g0134-gradual.maintenance
PLANNER="$ROOT/tools/topup_sbaq_g0134_20260816.py"
LOG_PREFIX=sbaq_g0134_gradual
RUN_PREFIX=sbaq-g0134-gradual
LEGACY_RUN_PREFIX=sbaq-g0734-gradual
MIN_AVAILABLE_KB=8388608
MIN_DISK_AVAIL_KB=15728640
MAX_LOAD_ONE=8
MAX_CONCURRENT_RUNS=2
FAILURE_COOLDOWN_HOURS=2
FANOUT_GRACE_MINUTES=15
ACTIVE_STATES="'pending','starting','running','pausing','paused','resuming','cancelling'"

log() {
  printf '%s %s %s\n' "$(date '+%F %T %Z')" "$LOG_PREFIX" "$*"
}

exec 9>"$LOCK_FILE"
if ! /usr/bin/flock -n 9; then
  log "skip reason=lock_busy"
  exit 0
fi

if [[ -e "$MAINTENANCE_FILE" ]]; then
  log "skip reason=maintenance file=$MAINTENANCE_FILE"
  exit 0
fi

if [[ -e "$TOKEN_FILE" ]]; then
  log "skip reason=acceptance_token_in_use"
  exit 0
fi

available_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
load_one=$(awk '{print $1}' /proc/loadavg)
disk_avail_kb=$(/usr/bin/df --output=avail / | /usr/bin/tail -1 | /usr/bin/tr -d ' ')
if (( available_kb < MIN_AVAILABLE_KB )); then
  log "skip reason=memory available_kb=$available_kb threshold_kb=$MIN_AVAILABLE_KB"
  exit 0
fi
if (( disk_avail_kb < MIN_DISK_AVAIL_KB )); then
  log "skip reason=disk avail_kb=$disk_avail_kb threshold_kb=$MIN_DISK_AVAIL_KB"
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

# 2026-09-01 用户拍板放行并行：前台 run 不再全局阻断渐进补采，只屏蔽其占用
# 的浏览器实例（按 browser_fence 活跃租约判定）；内存/磁盘/负载门仍然 fail-closed。
busy_instances=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT platform FROM platform.browser_fence WHERE released_at IS NULL AND expires_at > now()"
)
foreground_runs=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT count(*) FROM platform.collection_run WHERE state IN ($ACTIVE_STATES) AND idempotency_key NOT LIKE '$RUN_PREFIX-%' AND idempotency_key NOT LIKE '$LEGACY_RUN_PREFIX-%'"
)
if (( foreground_runs > 0 )); then
  log "notice foreground_collection count=$foreground_runs busy_browsers=$(printf '%s' "$busy_instances" | tr '\n' ',' )"
fi

active_gradual_runs=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT count(*) FROM platform.collection_run WHERE state IN ($ACTIVE_STATES) AND (idempotency_key LIKE '$RUN_PREFIX-%' OR idempotency_key LIKE '$LEGACY_RUN_PREFIX-%')"
)
if (( active_gradual_runs >= MAX_CONCURRENT_RUNS )); then
  log "skip reason=gradual_capacity active=$active_gradual_runs maximum=$MAX_CONCURRENT_RUNS"
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

if [[ "$(systemctl is-active geo-platform-v2-worker.service)" != active ]]; then
  log "skip reason=worker_inactive"
  exit 0
fi

legs=(doubao-bj doubao-sh deepseek-bj deepseek-sh yiyan-bj yiyan-sh)
last_key=$(
  "$PSQL" "$psql_dsn" -X -Atc \
    "SELECT idempotency_key FROM platform.collection_run WHERE idempotency_key LIKE '$RUN_PREFIX-%' OR idempotency_key LIKE '$LEGACY_RUN_PREFIX-%' ORDER BY created_at DESC LIMIT 1"
)
start_index=0
for index in "${!legs[@]}"; do
  if [[ "$last_key" == "$RUN_PREFIX-${legs[$index]}-"* || "$last_key" == "$LEGACY_RUN_PREFIX-${legs[$index]}-"* ]]; then
    start_index=$(( (index + 1) % ${#legs[@]} ))
    break
  fi
done

leg=""
for offset in "${!legs[@]}"; do
  candidate=${legs[$(( (start_index + offset) % ${#legs[@]} ))]}
  candidate_mode=deep_think
  if [[ "$candidate" == doubao-* ]]; then
    candidate_mode=normal
  fi
  browser_unit="geo-platform-v2-browser@${candidate//-/_}.service"
  if [[ "$(systemctl is-active "$browser_unit")" != active ]]; then
    log "candidate_skip reason=browser_inactive leg=$candidate unit=$browser_unit"
    continue
  fi

  active_on_leg=$(
    "$PSQL" "$psql_dsn" -X -Atc \
      "SELECT count(*) FROM platform.collection_run WHERE state IN ($ACTIVE_STATES) AND (idempotency_key LIKE '$RUN_PREFIX-$candidate-%' OR idempotency_key LIKE '$LEGACY_RUN_PREFIX-$candidate-%')"
  )
  if (( active_on_leg > 0 )); then
    log "candidate_skip reason=leg_active leg=$candidate count=$active_on_leg"
    continue
  fi

  recent_leg_runs=$(
    "$PSQL" "$psql_dsn" -X -Atc \
      "SELECT count(*) FROM platform.collection_run WHERE (idempotency_key LIKE '$RUN_PREFIX-$candidate-%' OR idempotency_key LIKE '$LEGACY_RUN_PREFIX-$candidate-%') AND updated_at >= now() - make_interval(mins => $FANOUT_GRACE_MINUTES)"
  )
  if (( recent_leg_runs > 0 )); then
    log "candidate_skip reason=fanout_grace leg=$candidate recent_runs=$recent_leg_runs minutes=$FANOUT_GRACE_MINUTES"
    continue
  fi

  browser_key=${candidate//-/_}
  if printf '%s\n' "$busy_instances" | /usr/bin/grep -qx "$browser_key"; then
    log "candidate_skip reason=browser_fenced_external leg=$candidate instance=$browser_key"
    continue
  fi
  browser_state=$(
    "$PSQL" "$psql_dsn" -X -F '|' -Atc \
      "SELECT activity, CASE WHEN breaker_until > now() THEN 1 ELSE 0 END, coalesce(to_char(breaker_until AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS'),'none'), CASE WHEN muted_until > now() THEN 1 ELSE 0 END, coalesce(to_char(muted_until AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS'),'none') FROM platform.collection_browser WHERE instance_key='$browser_key'"
  )
  if [[ -z "$browser_state" ]]; then
    log "candidate_skip reason=browser_unregistered leg=$candidate instance=$browser_key"
    continue
  fi
  IFS='|' read -r browser_activity breaker_active breaker_until muted_active muted_until <<<"$browser_state"
  if [[ "$browser_activity" != idle ]]; then
    log "candidate_skip reason=browser_not_idle leg=$candidate activity=$browser_activity"
    continue
  fi
  if [[ "$muted_active" == 1 ]]; then
    log "candidate_skip reason=browser_muted leg=$candidate muted_until=$muted_until"
    continue
  fi
  if [[ "$breaker_active" == 1 ]]; then
    # 配额按账号×mode 生效。最近一次同批根因若是另一模式的 quota，不能拿
    # 专家配额 breaker 阻断快速补采；验证码/登录/禁言等账号级墙仍 fail-closed。
    breaker_root=$(
      "$PSQL" "$psql_dsn" -X -F '|' -Atc \
        "WITH latest_breaker AS (SELECT event.created_at FROM platform.collection_account_event event JOIN platform.collection_browser browser ON browser.id=event.browser_id WHERE browser.instance_key='$browser_key' AND event.event_type='breaker' ORDER BY event.created_at DESC LIMIT 1) SELECT coalesce(wall.new_value->>'wall_type',''),coalesce(wall.new_value->>'mode','') FROM platform.collection_account_event wall JOIN platform.collection_browser browser ON browser.id=wall.browser_id CROSS JOIN latest_breaker root WHERE browser.instance_key='$browser_key' AND wall.event_type='wall_hit' AND wall.created_at BETWEEN root.created_at - interval '5 minutes' AND root.created_at + interval '5 minutes' ORDER BY wall.created_at DESC LIMIT 1"
    )
    IFS='|' read -r breaker_wall_type breaker_wall_mode <<<"$breaker_root"
    if [[ "$breaker_wall_type" == wall_quota && -n "$breaker_wall_mode" && "$breaker_wall_mode" != "$candidate_mode" ]]; then
      log "candidate_allow reason=quota_mode_mismatch leg=$candidate requested_mode=$candidate_mode blocked_mode=$breaker_wall_mode breaker_until=$breaker_until"
    else
      log "candidate_skip reason=browser_breaker leg=$candidate mode=$candidate_mode breaker_until=$breaker_until root_wall=${breaker_wall_type:-unknown} root_mode=${breaker_wall_mode:-unknown}"
      continue
    fi
  fi

  recent_severe_failures=$(
    "$PSQL" "$psql_dsn" -X -Atc \
      "SELECT count(*) FROM platform.collection_run run JOIN platform.monitoring_config_version version ON version.id=run.config_version_id WHERE (run.idempotency_key LIKE '$RUN_PREFIX-$candidate-%-run' OR run.idempotency_key LIKE '$LEGACY_RUN_PREFIX-$candidate-%-run') AND (version.snapshot_json::jsonb -> 'modes') ? '$candidate_mode' AND run.updated_at >= now() - make_interval(hours => $FAILURE_COOLDOWN_HOURS) AND (run.state IN ('failed','cancelled') OR (run.failed_tasks > 0 AND run.failed_tasks * 2 >= run.total_tasks))"
  )
  if (( recent_severe_failures > 0 )); then
    log "candidate_skip reason=leg_cooldown leg=$candidate mode=$candidate_mode recent_severe_failures=$recent_severe_failures"
    continue
  fi

  candidate_plan=$(GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$PLANNER" --leg "$candidate" --max-queries 4)
  health_ok=$("$PYTHON" -c 'import json, sys; print(1 if json.load(sys.stdin)["health"]["ok"] else 0)' <<<"$candidate_plan")
  if (( health_ok == 0 )); then
    health_reason=$("$PYTHON" -c 'import json, sys; print(",".join(json.load(sys.stdin)["health"]["reasons"]))' <<<"$candidate_plan")
    log "candidate_skip reason=health_gate leg=$candidate detail=$health_reason"
    continue
  fi
  health_warning=$("$PYTHON" -c 'import json, sys; print(",".join(json.load(sys.stdin)["health"].get("warnings", [])))' <<<"$candidate_plan")
  if [[ -n "$health_warning" ]]; then
    log "candidate_allow reason=health_warning leg=$candidate detail=$health_warning"
  fi
  launch_count=$("$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["launch_query_count"])' <<<"$candidate_plan")
  if (( launch_count == 0 )); then
    log "candidate_skip reason=leg_complete leg=$candidate"
    continue
  fi

  leg=$candidate
  break
done

if [[ -z "$leg" ]]; then
  log "skip reason=no_eligible_leg"
  exit 0
fi

cleanup_token() {
  if [[ ! -f "$TOKEN_FILE" ]]; then
    return
  fi
  GRADUAL_TOKEN_FILE="$TOKEN_FILE" "$PYTHON" -c 'import httpx, os, pathlib
p = pathlib.Path(os.environ["GRADUAL_TOKEN_FILE"])
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

# Re-read the DB health snapshot immediately before minting a privileged session.  The
# planner repeats the same fail-closed gate immediately before freeze and before run.
pre_mint_plan=$(GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$PLANNER" --leg "$leg" --max-queries 4)
pre_mint_health_ok=$("$PYTHON" -c 'import json, sys; print(1 if json.load(sys.stdin)["health"]["ok"] else 0)' <<<"$pre_mint_plan")
if (( pre_mint_health_ok == 0 )); then
  pre_mint_health_reason=$("$PYTHON" -c 'import json, sys; print(",".join(json.load(sys.stdin)["health"]["reasons"]))' <<<"$pre_mint_plan")
  log "skip reason=pre_mint_health_gate leg=$leg detail=$pre_mint_health_reason"
  exit 0
fi

GEO_MINT_TOKEN_FILE="$TOKEN_FILE" GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$ROOT/tools/mint_acceptance_session.py" >/dev/null
pass_id="auto-$(date '+%Y%m%dT%H%M')"
output=$(
  GEO_GRADUAL_TOKEN_FILE="$TOKEN_FILE" GEO_POSTGRES_DSN="$dsn_raw" "$PYTHON" "$PLANNER" \
    --leg "$leg" \
    --pass-id "$pass_id" \
    --max-queries 4 \
    --launch
)
log "launch leg=$leg mode=$($PYTHON -c 'import json, sys; print(json.load(sys.stdin)["mode"])' <<<"$output") payload=$output"
