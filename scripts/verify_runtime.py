import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from temporalio.client import Client

from workflows.definitions.health import PlatformHealthWorkflow

ROOT = Path(__file__).parents[1]
URLS = {
    "api_health": "http://127.0.0.1:45200/api/v2/health",
    "api_readiness": "http://127.0.0.1:45200/api/v2/readiness",
    "customer_web": "http://127.0.0.1:45101/platform/customer/",
    "operations_web": "http://127.0.0.1:45102/platform/operations/",
    "report_studio": "http://127.0.0.1:45103/platform/reports/",
    "intelligence_web": "http://127.0.0.1:45104/platform/intelligence/",
    "temporal_ui": "http://127.0.0.1:18080",
    "minio_health": "http://127.0.0.1:19000/minio/health/live",
    "clickhouse_health": "http://127.0.0.1:18123/ping",
}


async def verify() -> None:
    opener = build_opener(ProxyHandler({}))
    urls: dict[str, dict[str, object]] = {}
    for name, url in URLS.items():
        with opener.open(url, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            urls[name] = {"url": url, "status": response.status, "body_prefix": body[:80]}

    client = await Client.connect("127.0.0.1:17233")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    workflow_result = await client.execute_workflow(
        PlatformHealthWorkflow.run,
        "runtime-verified",
        id=f"platform-health/tnt_01K10D5Z70X5T9V9C8ZJS1R0AB/s00/run_{stamp}",
        task_queue="geo-platform-v2",
    )
    process = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "ps",
        "--format",
        "json",
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"docker compose ps failed with {process.returncode}")
    compose = stdout.decode()
    evidence = {
        "verified_at": datetime.now(UTC).isoformat(),
        "urls": urls,
        "temporal_workflow_result": workflow_result,
        "compose": [json.loads(line) for line in compose.splitlines() if line],
    }
    (ROOT / "tests/runtime-verification.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(verify())
