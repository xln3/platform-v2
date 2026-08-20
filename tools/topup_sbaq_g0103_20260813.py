"""盛邦 G01–G03 缺口补采（20260813，用户指令：补到 2 遍后停所有采集）。

按 capture_time 窗口实测缺口（每 cell 恰差 1 观测，全在豆包腿）：
- 豆包×上海 6 题；豆包×北京 2 题。
冻结两个小配置并发起 run。用法：--freeze-launch（幂等键固定，可安全重放）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import httpx
from launch_sbaq_formal_20260813 import ALL_GROUPS

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")
DATE_TAG = "20260813T1640"

# (组名, [该组需补的问题]) —— 文本取自 ALL_GROUPS，逐字一致
NEED_SH = {
    "高校双非资产排查可以找什么公司做": ["高校双非资产排查可以找什么公司做"],
    "资产漏洞一体化融合治理平台厂商": [
        "资产漏洞一体化融合治理平台厂商",
        "采购资产与漏洞联动治理平台时如何评估厂商能力",
        "资产台账和漏洞管理想一个平台搞定，国内谁家做得好？",
    ],
}
NEED_BJ = {
    "资产漏洞一体化融合治理平台厂商": [
        "资产台账和漏洞管理想一个平台搞定，国内谁家做得好？",
    ],
}


def _payload_groups(need: dict[str, list[str]]) -> list[dict]:
    """从 ALL_GROUPS 取逐字文本并校验，杜绝手抄误差。"""
    canonical = {name: questions for name, questions in ALL_GROUPS}
    groups = []
    for name, wanted in need.items():
        assert name in canonical, name
        for q in wanted:
            assert q in canonical[name], f"{q} not verbatim in {name}"
        groups.append(
            {
                "name": name,
                "items": [{"text": q, "priority": canonical[name].index(q) + 1} for q in wanted],
            }
        )
    return groups


def freeze(client: httpx.Client, tag: str, region: str, need: dict[str, list[str]]) -> str:
    from datetime import UTC, datetime

    body = {
        "query_groups": _payload_groups(need),
        "regions": [region],
        "models": ["doubao"],
        "modes": ["deep_think"],
        "frequency": "manual",
        "effective_at": datetime.now(UTC).isoformat(),
    }
    resp = client.post(
        f"/api/v2/projects/{PROJECT}/config/freeze",
        headers={"Idempotency-Key": f"sbaq-topup-cfg-{tag}-{DATE_TAG}"},
        json=body,
    )
    resp.raise_for_status()
    return str(resp.json()["pub_id"])


def launch(client: httpx.Client, tag: str, cfv: str) -> str:
    resp = client.post(
        "/api/v2/collection/runs",
        headers={"Idempotency-Key": f"sbaq-topup-run-{tag}-{DATE_TAG}"},
        json={
            "project_pub_id": PROJECT,
            "config_version_pub_id": cfv,
            "requires_intervention": False,
        },
    )
    resp.raise_for_status()
    return str(resp.json()["workflow_id"]).rsplit("/", 1)[-1]


def main() -> None:
    token = TOKEN_FILE.read_text().strip()
    with httpx.Client(
        base_url=BASE, verify=False, cookies={"__Host-geo_session": token}, timeout=60
    ) as client:
        session = client.get("/api/v2/identity/session")
        session.raise_for_status()
        for tag, region, need in [("doubao-sh", "上海", NEED_SH), ("doubao-bj", "北京", NEED_BJ)]:
            cfv = freeze(client, tag, region, need)
            run = launch(client, tag, cfv)
            print(json.dumps({"tag": tag, "cfv": cfv, "run": run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
