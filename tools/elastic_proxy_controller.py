"""Periodic region-capacity acquisition with explicit paid-order count limits."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from tools.activate_elastic_proxy import activate_verified_proxy
from workflows.activities.elastic_proxy import (
    HARD_DAILY_LIMIT,
    ProxyPurchaseBudget,
    ensure_region_capacity,
)
from workflows.activities.proxy_notifications import notify_proxy_event, notify_purchase_needed
from workflows.activities.region_proxy_router import ENV_ROUTING_MODE, RegionProxyRouter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", default=os.getenv("GEO_ELASTIC_PROXY_AUTO_BUY", "0") == "1"
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        default=os.getenv("GEO_ELASTIC_PROXY_AUTO_ACTIVATE", "0") == "1",
    )
    parser.add_argument(
        "--region",
        action="append",
        default=[
            value.strip()
            for value in os.getenv("GEO_ELASTIC_PROXY_REGIONS", "").split(",")
            if value.strip()
        ],
    )
    parser.add_argument(
        "--max-purchases-per-day",
        type=int,
        default=int(os.getenv("GEO_ELASTIC_PROXY_MAX_PURCHASES_PER_DAY", "0")),
    )
    parser.add_argument(
        "--journal", type=Path, default=Path("runtime/elastic-proxy-budget.sqlite3")
    )
    args = parser.parse_args()
    if args.apply and not 1 <= args.max_purchases_per_day <= HARD_DAILY_LIMIT:
        parser.error("automatic paid execution requires a daily purchase limit between 1 and 3")
    if args.activate and not args.apply:
        parser.error("relay activation requires --apply")
    if os.getenv(ENV_ROUTING_MODE) != "wukong":
        parser.error(f"{ENV_ROUTING_MODE}=wukong is required")
    budget = ProxyPurchaseBudget(args.journal)
    try:
        for region in sorted(set(args.region)):
            result = ensure_region_capacity(
                region,
                router=RegionProxyRouter(),
                budget=budget,
                daily_limit=args.max_purchases_per_day,
                apply=args.apply,
                now=datetime.now(UTC),
                notify_needed=notify_purchase_needed,
            )
            if args.activate and result["state"] in {"available", "acquired"}:
                try:
                    verified = RegionProxyRouter().resolve("doubao", region)
                    result["activation"] = activate_verified_proxy(verified)
                except Exception as exc:
                    result["activation"] = f"failed:{type(exc).__name__}"
            if (
                args.apply
                and result["state"] != "available"
                and result.get("reason") != "notification_pending"
            ):
                notify_proxy_event(
                    region,
                    datetime.now(UTC),
                    event=str(result.get("reason") or result["state"]),
                    summary=f"悟空代理补充结果：{json.dumps(result, ensure_ascii=False)}",
                )
            print(json.dumps(result), flush=True)
    finally:
        budget.close()


if __name__ == "__main__":
    main()
