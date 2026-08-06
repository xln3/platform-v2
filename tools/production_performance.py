from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * quantile) - 1))
    return round(ordered[index], 3)


async def benchmark(
    client: httpx.AsyncClient,
    path: str,
    *,
    headers: dict[str, str],
    requests: int,
    concurrency: int,
) -> dict[str, object]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[int, int] = {}

    async def one() -> None:
        async with semaphore:
            started = time.perf_counter()
            response = await client.get(path, headers=headers)
            latencies.append((time.perf_counter() - started) * 1_000)
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(requests)))
    duration = time.perf_counter() - started
    return {
        "path": path,
        "requests": requests,
        "concurrency": concurrency,
        "duration_seconds": round(duration, 3),
        "requests_per_second": round(requests / duration, 3),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 3),
        },
        "statuses": {str(code): count for code, count in sorted(statuses.items())},
        "passed": statuses == {200: requests},
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://127.0.0.1:8443")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    engine = create_engine(os.environ["GEO_POSTGRES_DSN"])
    with engine.connect() as connection:
        tenant, subject = connection.execute(
            text(
                """
                SELECT t.pub_id,u.subject
                FROM platform.membership m
                JOIN platform.tenant t ON t.id=m.tenant_id
                JOIN platform.app_user u ON u.id=m.user_id
                WHERE m.role='admin' AND m.state='active' AND m.revoked_at IS NULL
                LIMIT 1
                """
            )
        ).one()
    identity_headers = {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": subject,
        "X-Actor-Role": "admin",
    }
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=args.base_url,
        verify=False,
        trust_env=False,
        timeout=30,
        limits=limits,
    ) as client:
        results = [
            await benchmark(
                client,
                "/api/v2/health",
                headers={},
                requests=args.requests,
                concurrency=args.concurrency,
            ),
            await benchmark(
                client,
                "/api/v2/projects?limit=50",
                headers=identity_headers,
                requests=args.requests,
                concurrency=args.concurrency,
            ),
        ]
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": "production",
        "base_url": args.base_url,
        "identity_values_included": False,
        "results": results,
        "passed": all(bool(result["passed"]) for result in results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"passed": evidence["passed"], "results": results}))


if __name__ == "__main__":
    asyncio.run(main())
