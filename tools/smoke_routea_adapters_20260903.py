#!/usr/bin/env python3
"""路线 A 接口扩展（20260903）上线后 live 冒烟：4 平台 × 1 中性题 × 北京。

目的：验证元宝 detail 引用 URL 合并、豆包 chain/single 引用恢复、文心
referenceList 召回全集、通义 sse_raw 补采在真实采集管线里生效。

口径：项目=盛邦（prj_68ER9J6QBX054EAX52G7BEF7PH）不动的原则下选哪个项目？
——冒烟用费列罗客户项目会有品牌词污染顾虑，但本题是中性的「请介绍一下什么是
搜索引擎优化」，对任何项目的品牌测量无影响（mention 按品牌词表匹配）。用
费列罗项目（client-fll 本轮主线）单臂单题。

认证：mint_acceptance_session.py 铸的短期 admin 会话 cookie（用后回收）。

用法：
  sudo .venv/bin/python tools/mint_acceptance_session.py   # 铸 token 到 /tmp
  .venv/bin/python tools/smoke_routea_adapters_20260903.py
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://127.0.0.1:8443"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")

# 费列罗项目（client-fll 主线）；中性单题不污染品牌测量
PROJECT = "prj_fll_placeholder"  # 运行时由 --project 覆盖或常量替换
QUESTION = "请介绍一下什么是搜索引擎优化"
PLATFORMS = ["doubao", "yuanbao", "yiyan", "tongyi"]


def main() -> None:
    import os

    project = os.environ.get("SMOKE_PROJECT_PUB_ID", PROJECT)
    if project.startswith("prj_fll_placeholder"):
        raise SystemExit("set SMOKE_PROJECT_PUB_ID")
    token = TOKEN_FILE.read_text().strip()
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    with httpx.Client(
        base_url=BASE,
        verify=False,
        cookies={"__Host-geo_session": token},
        timeout=30,
    ) as client:
        session = client.get("/api/v2/identity/session")
        session.raise_for_status()
        body = {
            "query_groups": [
                {"name": "路线A冒烟-中性单题", "items": [{"text": QUESTION, "priority": 1}]}
            ],
            "regions": ["北京"],
            "models": PLATFORMS,
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        }
        resp = client.post(
            f"/api/v2/projects/{project}/config/freeze",
            headers={"Idempotency-Key": f"routea-smoke-cfg-{stamp}"},
            json=body,
        )
        resp.raise_for_status()
        cfv = str(resp.json()["pub_id"])
        run = client.post(
            "/api/v2/collection/runs",
            headers={"Idempotency-Key": f"routea-smoke-run-{stamp}"},
            json={
                "project_pub_id": project,
                "config_version_pub_id": cfv,
                "requires_intervention": False,
            },
        )
        run.raise_for_status()
        print(json.dumps({"cfv": cfv, "run": run.json()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
