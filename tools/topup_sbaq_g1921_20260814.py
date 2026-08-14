#!/usr/bin/env python3
"""Plan and launch exact Appendix III G19-G21 deficit top-ups.

Coverage is counted only from eligible, non-degraded analytics answers in the
formal collection window.  Each invocation launches at most one new
observation per still-deficient question, so repeated invocations converge to
two independent observations without replaying already-complete cells.
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
from launch_sbaq_formal_20260813 import GROUPS_APPENDIX3  # noqa: E402

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")
WINDOW_START = "2026-08-12 17:59:00+00"
TARGET = 2
DATE_TAG = "20260814"

GROUPS = GROUPS_APPENDIX3[:3]
LEGS: dict[str, tuple[str, str]] = {
    "doubao-bj": ("doubao", "北京"),
    "doubao-sh": ("doubao", "上海"),
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
    questions = [question for _, items in GROUPS for question in items]
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


def deficit_groups(counts: dict[str, int], max_queries: int | None) -> list[dict[str, object]]:
    remaining = max_queries
    groups: list[dict[str, object]] = []
    for name, questions in GROUPS:
        items: list[dict[str, object]] = []
        for priority, question in enumerate(questions, 1):
            if counts[question] >= TARGET:
                continue
            if remaining is not None and remaining <= 0:
                break
            items.append({"text": question, "priority": priority})
            if remaining is not None:
                remaining -= 1
        if items:
            groups.append({"name": name, "items": items})
        if remaining == 0:
            break
    return groups


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
    key = f"sbaq-g1921-topup-{leg}-{pass_id}-{DATE_TAG}"
    with _client() as client:
        client.get("/api/v2/identity/session").raise_for_status()
        frozen = client.post(
            f"/api/v2/projects/{PROJECT}/config/freeze",
            headers={"Idempotency-Key": f"{key}-cfg"},
            json={
                "query_groups": groups,
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
    parser.add_argument("--max-queries", type=int)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    if args.max_queries is not None and args.max_queries < 1:
        parser.error("--max-queries must be positive")
    if args.launch and not args.pass_id:
        parser.error("--pass-id is required with --launch")

    counts = coverage(args.leg)
    groups = deficit_groups(counts, args.max_queries)
    plan = {
        "leg": args.leg,
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
