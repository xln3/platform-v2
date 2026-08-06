"""Operator CLI for explicit, audited-by-terminal Wukong paid acquisition.

This command cannot spend unless ``--confirm-spend`` is present. It prints no
proxy credentials; the worker-local cache is the only credential-bearing output.
"""

from __future__ import annotations

import argparse
import json

from workflows.activities.region_proxy_router import RegionProxyError, RegionProxyRouter


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage V2 regional Wukong proxies")
    parser.add_argument(
        "action",
        choices=["resolve", "acquire-paid", "clear-purchase-intent"],
    )
    parser.add_argument("--region", required=True, help="CN-BJ / 110000 / 北京 等任务地域")
    parser.add_argument(
        "--platform",
        choices=["doubao", "deepseek", "yuanbao", "tongyi", "yiyan"],
        default="doubao",
    )
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--confirm-no-order", action="store_true")
    args = parser.parse_args()
    if args.action == "acquire-paid" and not args.confirm_spend:
        parser.error("paid acquisition requires --confirm-spend")
    if args.action == "clear-purchase-intent" and not args.confirm_no_order:
        parser.error("clearing a purchase intent requires --confirm-no-order")
    try:
        if args.action == "acquire-paid":
            result = RegionProxyRouter().acquire_paid(args.region, confirm_spend=True)
        elif args.action == "clear-purchase-intent":
            result = RegionProxyRouter().clear_purchase_intent(
                args.region,
                confirm_no_order=True,
            )
        else:
            result = RegionProxyRouter().resolve(args.platform, args.region)
    except RegionProxyError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "region": result.requested_region,
                "region_gb": result.region_gb,
                "city": result.city,
                "source": result.source,
                "provider_action": result.provider_action,
                "observed_gb": result.observed_gb,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
