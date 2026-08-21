#!/usr/bin/env python3
"""Plan and launch conservative G01-G34 deficit top-ups for all six legs.

Only eligible, non-degraded analytics answers count toward the target.  The scheduler
enforces a post-run fanout grace period so a successful observation is not immediately
scheduled again while analytics ingestion catches up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).parent))
from launch_sbaq_formal_20260813 import ALL_GROUPS  # noqa: E402

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")
WINDOW_START = "2026-08-12 17:59:00+00"
TARGET = 2
DATE_TAG = "20260816"
DEFAULT_BATCH_SIZE = 4
MAX_BATCH_SIZE = 8
REGION_PROBE_MAX_AGE = timedelta(minutes=25)
_SHANGHAI = ZoneInfo("Asia/Shanghai")

GROUPS: list[tuple[int, str, list[str]]] = [
    (group_number, name, questions)
    for group_number, (name, questions) in enumerate(ALL_GROUPS, start=1)
]
LEGS: dict[str, tuple[str, str]] = {
    "doubao-bj": ("doubao", "北京"),
    "doubao-sh": ("doubao", "上海"),
    "deepseek-bj": ("deepseek", "北京"),
    "deepseek-sh": ("deepseek", "上海"),
    "yiyan-bj": ("yiyan", "北京"),
    "yiyan-sh": ("yiyan", "上海"),
}
MODE_LABELS = {"normal": "快速模式", "deep_think": "深度思考"}


class LaunchHealthError(RuntimeError):
    """The gradual top-up admission gate rejected a leg."""


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _quota_value(
    account: Mapping[str, object],
    *,
    used_key: str,
    quota_key: str,
    now: datetime,
) -> tuple[int, int | None]:
    used = int(str(account.get(used_key) or 0))
    quota_raw = account.get(quota_key)
    quota = int(str(quota_raw)) if quota_raw is not None else None
    reset_at = account.get("quota_reset_at")
    if isinstance(reset_at, datetime) and _as_utc(reset_at) <= now:
        if used_key == "used_today":
            used = 0
        else:
            previous = reset_at.astimezone(_SHANGHAI)
            current = now.astimezone(_SHANGHAI)
            if used_key == "used_week" and previous.isocalendar()[:2] != current.isocalendar()[:2]:
                used = 0
            if used_key == "used_year" and previous.year != current.year:
                used = 0
    return used, quota


def evaluate_launch_health(
    *,
    region: Mapping[str, object] | None,
    browser: Mapping[str, object] | None,
    accounts: Sequence[Mapping[str, object]],
    expected_browser_key: str,
    platform: str,
    region_gb: str,
    mode: str,
    now: datetime,
) -> dict[str, object]:
    """Pure, fail-closed health policy used before mint/freeze/run.

    The latest relay sample must itself be recent and successful even while the durable
    region state is protected by hysteresis.  A browser whose breaker TTL elapsed remains
    blocked once its failure streak reached the breaker threshold; only an explicit
    recovery/success that clears the streak can re-admit unattended work.
    """
    now = _as_utc(now)
    reasons: list[str] = []
    warnings: list[str] = []
    if region is None:
        reasons.append("region_unregistered")
    else:
        if region.get("state") != "ok":
            reasons.append(f"region_state_{region.get('state') or 'unknown'}")
        last_probe_at = region.get("last_probe_at")
        if region.get("last_probe_ok") is not True:
            reasons.append("region_latest_probe_not_ok")
        if not isinstance(last_probe_at, datetime):
            reasons.append("region_probe_missing")
        elif now - _as_utc(last_probe_at) > REGION_PROBE_MAX_AGE:
            reasons.append("region_probe_stale")

    if browser is None:
        reasons.append("browser_unregistered")
    else:
        if browser.get("instance_key") != expected_browser_key:
            reasons.append("browser_key_mismatch")
        if browser.get("platform") != platform:
            reasons.append("browser_platform_mismatch")
        if browser.get("region_gb") != region_gb:
            reasons.append("browser_region_mismatch")
        if browser.get("activity") != "idle":
            reasons.append(f"browser_activity_{browser.get('activity') or 'unknown'}")
        if int(str(browser.get("error_streak") or 0)) >= 3:
            reasons.append("browser_failure_unrecovered")
        breaker_until = browser.get("breaker_until")
        if isinstance(breaker_until, datetime) and _as_utc(breaker_until) > now:
            reasons.append("browser_breaker_active")
        muted_until = browser.get("muted_until")
        if muted_until is None and browser.get("activity") == "muted":
            reasons.append("browser_muted_manual")
        elif isinstance(muted_until, datetime) and _as_utc(muted_until) > now:
            reasons.append("browser_muted")

    if not accounts:
        if platform.strip().lower() == "doubao":
            # 豆包已经进入正式账号治理：缺少 platform_account 代表绑定尚未完成，
            # 不得退回 env 路径撞浏览器。其他平台仍处于分阶段迁移期，暂保留
            # legacy 路径并显式告警，避免 DeepSeek/Yiyan 因全表初始为空被饿死。
            reasons.append("account_unregistered")
        else:
            warnings.append("legacy_unmanaged")
    else:
        collectable = False
        for account in accounts:
            if account.get("phone_state") != "active":
                continue
            if account.get("browser_instance_key") != expected_browser_key:
                continue
            state = str(account.get("runtime_state") or "")
            if state == "muted":
                resume_at = account.get("muted_until")
                state = (
                    "idle"
                    if isinstance(resume_at, datetime) and _as_utc(resume_at) <= now
                    else state
                )
            elif state == "quota_exhausted":
                resume_at = account.get("quota_resume_at")
                state = (
                    "idle"
                    if isinstance(resume_at, datetime) and _as_utc(resume_at) <= now
                    else state
                )
            if state != "idle" or account.get("current_run_pub_id"):
                continue
            snapshot = account.get("quota_probe_json")
            raw_blocks = snapshot.get("mode_quota_blocks") if isinstance(snapshot, dict) else None
            if isinstance(raw_blocks, dict):
                raw_block = raw_blocks.get(mode)
                if isinstance(raw_block, dict):
                    resume_raw = raw_block.get("resume_at")
                    try:
                        resume_at = datetime.fromisoformat(str(resume_raw))
                        if _as_utc(resume_at) > now:
                            continue
                    except (TypeError, ValueError):
                        continue
            quota_ok = True
            for used_key, quota_key in (
                ("used_today", "quota_day"),
                ("used_week", "quota_week"),
                ("used_year", "quota_year"),
            ):
                used, quota = _quota_value(
                    account,
                    used_key=used_key,
                    quota_key=quota_key,
                    now=now,
                )
                if quota is not None and used >= quota:
                    quota_ok = False
                    break
            if quota_ok:
                collectable = True
                break
        if not collectable:
            reasons.append("no_collectable_account")

    return {
        "ok": not reasons,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "warnings": warnings,
        "governance": (
            "managed"
            if accounts
            else "required_unmanaged"
            if platform.strip().lower() == "doubao"
            else "legacy_unmanaged"
        ),
    }


def launch_health(leg: str, *, now: datetime | None = None) -> dict[str, object]:
    """Read one consistent DB snapshot and evaluate unattended-launch eligibility."""
    platform, region_name = LEGS[leg]
    region_gb = "110000" if region_name == "北京" else "310000"
    browser_key = leg.replace("-", "_")
    with psycopg.connect(_dsn(), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        region = connection.execute(
            """
            SELECT region_gb, state, last_probe_at, last_probe_ok,
                   probe_success_streak, probe_failure_streak
            FROM platform.collection_region
            WHERE region_gb = %s
            """,
            (region_gb,),
        ).fetchone()
        browser = connection.execute(
            """
            SELECT instance_key, platform, region_gb, activity, error_streak,
                   breaker_until, muted_until
            FROM platform.collection_browser
            WHERE instance_key = %s
            """,
            (browser_key,),
        ).fetchone()
        accounts = connection.execute(
            """
            SELECT account.runtime_state, account.current_run_pub_id,
                   account.muted_until, account.quota_resume_at,
                   account.browser_instance_key, account.quota_day,
                   account.quota_week, account.quota_year, account.used_today,
                   account.used_week, account.used_year, account.quota_reset_at,
                   account.quota_probe_json,
                   phone.state AS phone_state
            FROM platform.collection_platform_account AS account
            JOIN platform.collection_phone_account AS phone
              ON phone.id = account.phone_account_id
            WHERE account.platform = %s AND account.region_gb = %s
            """,
            (platform, region_gb),
        ).fetchall()
    return evaluate_launch_health(
        region=region,
        browser=browser,
        accounts=accounts,
        expected_browser_key=browser_key,
        platform=platform,
        region_gb=region_gb,
        mode=requested_mode(leg),
        now=now or datetime.now(UTC),
    )


def require_launch_health(leg: str, *, phase: str) -> dict[str, object]:
    health = launch_health(leg)
    if not health["ok"]:
        reasons = ",".join(cast(list[str], health["reasons"]))
        raise LaunchHealthError(f"{phase}:{leg}:{reasons}")
    return health


def requested_mode(leg: str) -> str:
    """豆包补采走快速；其余平台保持既定深度思考口径。"""
    return "normal" if LEGS[leg][0] == "doubao" else "deep_think"


def coverage_modes(leg: str) -> tuple[str, ...]:
    """豆包的既有专家答案与新增快速答案共同满足每题两遍目标。"""
    return ("deep_think", "normal") if LEGS[leg][0] == "doubao" else ("deep_think",)


def _dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "")
    if not value:
        raise SystemExit("GEO_POSTGRES_DSN is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def coverage(leg: str) -> dict[str, int]:
    model, region = LEGS[leg]
    modes = list(coverage_modes(leg))
    questions = [question for _, _, items in GROUPS for question in items]
    with psycopg.connect(_dsn()) as connection:
        rows = connection.execute(
            """
            SELECT query_text, count(*)::bigint AS count
            FROM analytics.answer
            WHERE project_pub_id = %s
              AND model = %s
              AND region = %s
              AND mode = ANY(%s::text[])
              AND eligible
              AND NOT degraded
              AND capture_time >= %s::timestamptz
              AND query_text = ANY(%s)
            GROUP BY query_text
            """,
            (PROJECT, model, region, modes, WINDOW_START, questions),
        ).fetchall()
    found = {str(question): int(count) for question, count in rows}
    return {question: found.get(question, 0) for question in questions}


def deficit_groups(counts: dict[str, int], max_queries: int) -> list[dict[str, object]]:
    remaining = max_queries
    groups: list[dict[str, object]] = []
    for group_number, name, questions in GROUPS:
        items: list[dict[str, object]] = []
        for priority, question in enumerate(questions, 1):
            if counts[question] >= TARGET:
                continue
            if remaining <= 0:
                break
            items.append({"text": question, "priority": priority})
            remaining -= 1
        if items:
            groups.append({"name": name, "group_number": group_number, "items": items})
        if remaining == 0:
            break
    return groups


def _api_groups(groups: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"name": str(group["name"]), "items": group["items"]} for group in groups]


def _client() -> httpx.Client:
    token = TOKEN_FILE.read_text().strip()
    return httpx.Client(
        base_url=BASE,
        verify=False,
        trust_env=False,
        cookies={"__Host-geo_session": token},
        timeout=60,
    )


def launch(leg: str, pass_id: str, groups: list[dict[str, object]]) -> tuple[str, str]:
    model, region = LEGS[leg]
    mode = requested_mode(leg)
    key = f"sbaq-g0134-gradual-{leg}-{pass_id}-{DATE_TAG}"
    require_launch_health(leg, phase="pre_freeze")
    with _client() as client:
        client.get("/api/v2/identity/session").raise_for_status()
        frozen = client.post(
            f"/api/v2/projects/{PROJECT}/config/freeze",
            headers={"Idempotency-Key": f"{key}-cfg"},
            json={
                "query_groups": _api_groups(groups),
                "regions": [region],
                "models": [model],
                "modes": [mode],
                "frequency": "manual",
                "effective_at": datetime.now(UTC).isoformat(),
            },
        )
        frozen.raise_for_status()
        config_pub_id = str(frozen.json()["pub_id"])
        # Recheck after the immutable freeze.  A health change in this narrow window may
        # leave an unused config snapshot, but must never create a collection run.
        require_launch_health(leg, phase="pre_run")
        started = client.post(
            "/api/v2/collection/runs",
            headers={"Idempotency-Key": f"{key}-run"},
            json={
                "project_pub_id": PROJECT,
                "config_version_pub_id": config_pub_id,
                "requires_intervention": False,
            },
        )
        started.raise_for_status()
        run_pub_id = str(started.json()["workflow_id"]).rsplit("/", 1)[-1]
    return config_pub_id, run_pub_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leg", choices=sorted(LEGS), required=True)
    parser.add_argument("--pass-id", help="unique auditable pass name; required with --launch")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_queries <= MAX_BATCH_SIZE:
        parser.error(f"--max-queries must be between 1 and {MAX_BATCH_SIZE}")
    if args.launch and not args.pass_id:
        parser.error("--pass-id is required with --launch")

    counts = coverage(args.leg)
    groups = deficit_groups(counts, args.max_queries)
    health = launch_health(args.leg)
    plan: dict[str, object] = {
        "scope": "G01-G34",
        "leg": args.leg,
        "mode": requested_mode(args.leg),
        "mode_label": MODE_LABELS[requested_mode(args.leg)],
        "target_count": len(counts) * TARGET,
        "covered_count": sum(min(TARGET, value) for value in counts.values()),
        "deficit": sum(max(0, TARGET - value) for value in counts.values()),
        "launch_query_count": sum(
            len(cast(list[dict[str, object]], group["items"])) for group in groups
        ),
        "query_groups": groups,
        "health": health,
    }
    if not args.launch:
        print(json.dumps(plan, ensure_ascii=False))
        return
    if not groups:
        raise SystemExit("coverage already complete; refusing empty launch")
    if not health["ok"]:
        reasons = ",".join(cast(list[str], health["reasons"]))
        raise SystemExit(f"launch health gate blocked: {reasons}")
    config_pub_id, run_pub_id = launch(args.leg, str(args.pass_id), groups)
    plan.update({"config_pub_id": config_pub_id, "run_pub_id": run_pub_id})
    print(json.dumps(plan, ensure_ascii=False))


if __name__ == "__main__":
    main()
