"""盛邦网空线 yiyan 臂重采（20260812）：表格 markdown 序列化修复（c1bd252）后验证轮。

口径与 20260810 冻结配置 A 的 yiyan 半臂完全一致：同 16 题 × normal × 北京/上海
= 32 任务（「各问一次」）。新幂等键（sbaq-yiyan-tablemd-20260812-*），不复用
0810 的键——0810 run 保留作压平文本对照组。

认证：mint_acceptance_session.py 铸的短期 admin 会话 cookie（用后回收）。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")

# 与 launch_sbaq_wangkong_20260810.py 完全相同的 16 题（附录二原词+ABC 变体）
QUERIES = [
    "高校双非资产排查可以找什么公司做",
    "高校非传统IT资产与影子资产排查服务商推荐",
    "高校信息化部门如何选择未备案资产排查供应商",
    "我们学校好多没报备的IP和系统，找谁能帮忙查一遍？",
    "企业网络资产暴露面管理系统推荐",
    "互联网暴露面资产收敛与攻击面管理平台选型",
    "甲方安全团队评估攻击面管理（ASM）产品应关注哪些指标",
    "公司外网暴露的资产太多了，有没有好用的管理工具推荐？",
    "资产漏洞一体化融合治理平台厂商",
    "网络资产管理与漏洞治理一体化平台主流供应商",
    "采购资产与漏洞联动治理平台时如何评估厂商能力",
    "资产台账和漏洞管理想一个平台搞定，国内谁家做得好？",
    "国内网络空间资产搜索引擎哪家强",
    "国产网络空间测绘搜索引擎综合能力对比",
    "安全研究人员选择国内空间搜索引擎应考虑哪些因素",
    "像Shodan那种搜索引擎，国内有哪些替代？哪个数据全？",
]

GROUP_NAME = "盛邦-网空线16题（附录二原词+ABC变体）"
REGIONS = ["北京", "上海"]


def main() -> None:
    token = TOKEN_FILE.read_text().strip()
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
                {
                    "name": GROUP_NAME,
                    "items": [{"text": q, "priority": i + 1} for i, q in enumerate(QUERIES)],
                }
            ],
            "regions": REGIONS,
            "models": ["yiyan"],
            "modes": ["normal"],
            "frequency": "manual",
            "effective_at": datetime.now(UTC).isoformat(),
        }
        resp = client.post(
            f"/api/v2/projects/{PROJECT}/config/freeze",
            headers={"Idempotency-Key": "sbaq-yiyan-tablemd-cfg-20260812"},
            json=body,
        )
        resp.raise_for_status()
        cfv = str(resp.json()["pub_id"])
        print(json.dumps({"cfv": cfv}, ensure_ascii=False))

        run = client.post(
            "/api/v2/collection/runs",
            headers={"Idempotency-Key": "sbaq-yiyan-tablemd-run-20260812"},
            json={
                "project_pub_id": PROJECT,
                "config_version_pub_id": cfv,
                "requires_intervention": False,
            },
        )
        run.raise_for_status()
        print(json.dumps({"run": run.json()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
