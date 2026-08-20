#!/usr/bin/env bash
# 盛邦正式一轮周期补扇出（待办 C，零代码方案 1）：对项目内所有有完成题的
# 在途/异常 run，以及最近两小时完成的 G07-G34 渐进 run，做幂等 mint
#（ON CONFLICT 空操作 + 漂移校验），让已落库的问答尽早进入 analytics/运营端
# 可见。不做侧车（重活只跑一轮）。crontab 每 30 分钟一次。
set -euo pipefail
cd /home/xln/geo-system/platform-v2

DSN=$(/usr/bin/sudo -n /usr/bin/sed -n 's/^GEO_POSTGRES_DSN=//p' /etc/geo-platform-v2/platform.env | /usr/bin/head -1 | /usr/bin/sed 's/^postgresql+psycopg:\/\//postgresql:\/\//')
RUNS=$(/usr/bin/psql "$DSN" -t -A -c "
  select r.pub_id from platform.collection_run r
  join platform.project p on p.id=r.project_id
  where p.pub_id='prj_68ER9J6QBX054EAX52G7BEF7PH'
    and r.completed_tasks>0
    and (
      r.state in ('running','paused','failed','cancelled')
      or (
        r.idempotency_key like 'sbaq-g0734-gradual-%'
        and r.state in ('completed','completed_with_failures')
        and r.updated_at >= now() - interval '2 hours'
      )
    )
    and r.created_at >= '2026-08-12'::timestamptz
  order by r.created_at" | /usr/bin/paste -sd, -)
if [ -z "$RUNS" ]; then
  echo "$(date -Is) no candidate runs" 
  exit 0
fi
echo "$(date -Is) mint-only runs=$RUNS"
tools/run_with_platform_env.sh .venv/bin/python \
  tools/remediate_sbaq_formal_fanout_20260813.py --mint-only --runs "$RUNS"
