#!/usr/bin/env python3
"""Plan and launch conservative G07-G34 top-ups for DeepSeek and Yiyan.

Only eligible, non-degraded analytics answers in the formal collection window
count toward the two-observation target.  Each invocation schedules at most one
new observation per deficient query.  Operators should wait for analytics
fanout before the next pass so already-filled cells are not replayed.
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
DATE_TAG = "20260814"
DEFAULT_BATCH_SIZE = 4
MAX_BATCH_SIZE = 8

# Quotation groups are one-indexed.  G07-G34 is ALL_GROUPS[6:].
GROUPS: list[tuple[int, str, list[str]]] = [
    (group_number, name, questions)
    for group_number, (name, questions) in enumerate(ALL_GROUPS[6:], start=7)
]
LEGS: dict[str, tuple[str, str]] = {
    "deepseek-bj": ("deepseek", "北京"),
    "deepseek-sh": ("deepseek", "上海"),
    "yiyan-bj": ("yiyan", "北京"),
    "yiyan-sh": ("yiyan", "上海"),
}


def _dsn() -> str:
    value = os.environ.get("GEO_POSTGRES_DSN", "")
    if not value:
        raise SystemExit("GEO_POSTGRES_DSN is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def coverage(leg: str) -> dict[str, int]:
    model, region = LEGS[leg]
    questions = [question for _, _, items in GROUPS for question in items]
    with psycopg.connect(_dsn()) as connection:
        rows = connection.execute(
            """
            SELECT query_text, count(*)
            FROM analytics.answer
            WHERE project_pub_id = %s
              AND model = %s
              AND region = %s
              AND mode = 'deep_think'
              AND eligible
              AND NOT degraded
              AND capture_time >= %s::timestamptz
              AND query_text = ANY(%s)
            GROUP BY query_text
            """,
            (PROJECT, model, region, WINDOW_START, questions),
        ).fetchall()
    found = {str(question): int(count) for question, count in rows}
    return {question: found.get(question, 0) for question in questions}


def deficit_groups(
    counts: dict[str, int], max_queries: int
) -> list[dict[str, object]]:
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
    """Strip operator-only group numbers from the frozen config payload."""
    return [
        {"name": str(group["name"]), "items": group["items"]}
        for group in groups
    ]


def _client() -> httpx.Client:
    token = TOKEN_FILE.read_text().strip()
    return httpx.Client(
        base_url=BASE,
        verify=False,
        trust_env=False,
        cookies={"__Host-geo_session": token},
        timeout=60,
    )


def launch(
    leg: str, pass_id: str, groups: list[dict[str, object]]
) -> tuple[str, str]:
    model, region = LEGS[leg]
    key = f"sbaq-g0734-gradual-{leg}-{pass_id}-{DATE_TAG}"
    with _client() as client:
        client.get("/api/v2/identity/session").raise_for_status()
        frozen = client.post(
            f"/api/v2/projects/{PROJECT}/config/freeze",
            headers={"Idempotency-Key": f"{key}-cfg"},
            json={
                "query_groups": _api_groups(groups),
                "regions": [region],
                "models": [model],
                "modes": ["deep_think"],
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
        "scope": "G07-G34",
        "leg": args.leg,
        "target_count": len(counts) * TARGET,
        "eligible_count": sum(min(TARGET, value) for value in counts.values()),
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
