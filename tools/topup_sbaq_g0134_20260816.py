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
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import psycopg

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
    }
    if not args.launch:
        print(json.dumps(plan, ensure_ascii=False))
        return
    if not groups:
        raise SystemExit("coverage already complete; refusing empty launch")
    config_pub_id, run_pub_id = launch(args.leg, str(args.pass_id), groups)
    plan.update({"config_pub_id": config_pub_id, "run_pub_id": run_pub_id})
    print(json.dumps(plan, ensure_ascii=False))


if __name__ == "__main__":
    main()
