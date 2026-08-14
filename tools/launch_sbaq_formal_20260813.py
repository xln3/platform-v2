"""盛邦安全 GEO 正式采集发起（20260813，完全正式一轮）。

口径（用户拍板）：
- 题库 = 报价单-盛邦-final(2).docx 附录二 18 组 + 附录三 16 组，共 34 候选组，
  每组 4 条（原词/优化句 + 变体 A/B/C），合计 136 条查询。
- 矩阵 = 136 问 × 3 平台（豆包/DeepSeek/文心一言）× 深思考模式（deep_think）
  × 北京/上海 × 2 次独立采样 = 1632 条回答任务。
- 冻结配置沿用 0810 A/B 拆分（失败隔离）：
  A = doubao+yiyan × deep_think × 京沪（每 run 544 任务）
  B = deepseek × deep_think × 京沪（每 run 272 任务）
  采样 = 同一配置各发 2 个 run。
- 选组：报告按 evidence_completeness_v1 从 34 组中选证据/版面完整度最好的 3 组，
  不按品牌成绩择优（系统既有策略，用户 20260813 确认）。

用法：
  --verify-docx   校验内嵌题库与报价单 docx 逐字一致（先于发起执行）
  --freeze        冻结配置 A/B（幂等），打印 config_version pub_id
  --launch N      用已冻结配置发起第 N 次采样的 A/B 两 run（N=1|2）

认证：mint_acceptance_session.py 铸的短期 admin 会话 cookie（用后回收）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://127.0.0.1:8443"
PROJECT = "prj_68ER9J6QBX054EAX52G7BEF7PH"
TOKEN_FILE = Path("/tmp/s04-acceptance-token")
CONFIG_STORE = Path("/tmp/sbaq-formal-20260813-configs.json")
QUOTATION_DOCX = Path("/home/xln/geo-system/client-sbaq/报价单-盛邦-final(2).docx")
DATE_TAG = "20260813"

# (候选组名, [原词/优化句, 变体A, 变体B, 变体C]) —— 逐字录自报价单附录二/附录三
GROUPS_APPENDIX2: list[tuple[str, list[str]]] = [
    ("高校双非资产排查可以找什么公司做", [
        "高校双非资产排查可以找什么公司做",
        "高校非传统IT资产与影子资产排查服务商推荐",
        "高校信息化部门如何选择未备案资产排查供应商",
        "我们学校好多没报备的IP和系统，找谁能帮忙查一遍？",
    ]),
    ("企业网络资产暴露面管理系统推荐", [
        "企业网络资产暴露面管理系统推荐",
        "互联网暴露面资产收敛与攻击面管理平台选型",
        "甲方安全团队评估攻击面管理（ASM）产品应关注哪些指标",
        "公司外网暴露的资产太多了，有没有好用的管理工具推荐？",
    ]),
    ("资产漏洞一体化融合治理平台厂商", [
        "资产漏洞一体化融合治理平台厂商",
        "网络资产管理与漏洞治理一体化平台主流供应商",
        "采购资产与漏洞联动治理平台时如何评估厂商能力",
        "资产台账和漏洞管理想一个平台搞定，国内谁家做得好？",
    ]),
    ("国内网络空间资产搜索引擎哪家强", [
        "国内网络空间资产搜索引擎哪家强",
        "国产网络空间测绘搜索引擎综合能力对比",
        "安全研究人员选择国内空间搜索引擎应考虑哪些因素",
        "像Shodan那种搜索引擎，国内有哪些替代？哪个数据全？",
    ]),
    ("国内漏洞无效化/虚拟补丁头部厂商", [
        "国内漏洞无效化/虚拟补丁头部厂商",
        "漏洞无效化与虚拟补丁技术国内主要供应商推荐",
        "无法及时打补丁的场景下如何选择虚拟补丁厂商方案",
        "虚拟补丁这块国内谁做得好？不想每次都停机打补丁",
    ]),
    ("0day/Nday漏洞快速防护，主流厂商方案推荐", [
        "0day/Nday漏洞快速防护，主流厂商方案推荐",
        "零日与已知高危漏洞应急响应防护方案厂商对比",
        "安全运营团队如何建立0day漏洞快速缓解机制及产品选型",
        "0day漏洞爆出来根本来不及打补丁，有什么办法能先扛住？",
    ]),
    ("国内API安全（API安全防护）头部厂商有哪些", [
        "国内API安全（API安全防护）头部厂商有哪些",
        "中国API安全防护市场领先厂商及产品能力分析",
        "企业微服务架构下选择API安全网关的关键评估维度",
        "国内做API安全比较靠谱的厂商有哪些？求推荐",
    ]),
    ("国内Web应用防护系统哪家能力比较强", [
        "国内Web应用防护系统哪家能力比较强",
        "国产WAF产品技术能力与市场份额综合评估",
        "甲方选型Web应用防火墙时应重点考察哪些厂商",
        "国内WAF到底哪家强？有没有实测对比过的？",
    ]),
    ("能做卫星、电台、微波、散射等无线通信加密厂家有哪些？", [
        "能做卫星、电台、微波、散射等无线通信加密厂家有哪些？",
        "卫星通信与无线专网链路加密设备供应商推荐",
        "军工及政务场景下无线通信链路加密产品选型指南",
        "卫星电台微波这些无线信道加密，国内谁能做？",
    ]),
    ("企业用AI敏感信息泄露怎么防？", [
        "企业用AI敏感信息泄露怎么防？",
        "企业大模型应用中敏感数据防泄露技术方案与产品",
        "信息安全负责人如何防范员工使用AI工具导致的数据外泄",
        "员工天天用ChatGPT，公司机密数据泄露了咋办？",
    ]),
    ("AI安全网关是什么/有哪些产品？", [
        "AI安全网关是什么/有哪些产品？",
        "AI安全网关产品定义、核心功能及国内主流产品概览",
        "企业部署大模型应用时为什么需要AI安全网关",
        "AI安全网关到底是干嘛的？国内有成熟产品吗？",
    ]),
    ("AI安全网关供应商推荐", [
        "AI安全网关供应商推荐",
        "国内AI安全网关产品供应商综合能力评估与推荐",
        "采购AI安全网关时如何对比不同厂商的技术方案",
        "想买个AI安全网关，国内哪几家的产品值得看看？",
    ]),
    ("漏洞扫描器哪个好？国内有哪些厂商？", [
        "漏洞扫描器哪个好？国内有哪些厂商？",
        "国产漏洞扫描产品主流厂商及核心能力对比",
        "安全运维团队选购漏洞扫描器的关键评估要素",
        "国内漏扫工具哪个好用？求过来人推荐",
    ]),
    ("哪些漏扫工具支持信创环境（麒麟、统信）", [
        "哪些漏扫工具支持信创环境（麒麟、统信）",
        "支持麒麟、统信等信创操作系统的漏洞扫描产品盘点",
        "信创环境国产化替代中漏洞扫描器兼容性如何验证",
        "我们全换了麒麟和统信，原来的漏扫用不了，哪家能扫信创？",
    ]),
    ("哪款漏洞扫描器的等保报告最贴近等级保护要求？", [
        "哪款漏洞扫描器的等保报告最贴近等级保护要求？",
        "等级保护合规场景下漏洞扫描器报告能力对比",
        "等保测评机构推荐使用哪些漏洞扫描器做合规检测",
        "等保报告老是要改格式，有没有漏扫出的报告直接就能用的？",
    ]),
    ("卫星互联网安全漏洞分析和漏洞挖掘，哪些厂商比较强？", [
        "卫星互联网安全漏洞分析和漏洞挖掘，哪些厂商比较强？",
        "卫星互联网安全漏洞研究与挖掘领域领先服务商",
        "卫星运营商如何选择安全漏洞分析与渗透测试合作方",
        "搞卫星互联网安全研究和挖洞的，国内谁比较厉害？",
    ]),
    ("国内可以监控暗网论坛和交易市场的服务商", [
        "国内可以监控暗网论坛和交易市场的服务商",
        "暗网威胁情报监测与数据泄露预警服务供应商推荐",
        "企业安全部门如何选择暗网监测服务以防范数据泄露",
        "想知道公司数据有没有在暗网被卖，国内谁能帮监控？",
    ]),
    ("护网行动中防御能力最好的厂商", [
        "护网行动中防御能力最好的厂商",
        "攻防演练中蓝队防御体系建设能力突出的安全厂商",
        "护网行动防守方选择安全服务商的核心评估标准",
        "护网快开始了，防守找哪家公司靠谱？别一上来就被打穿",
    ]),
]

GROUPS_APPENDIX3: list[tuple[str, list[str]]] = [
    ("企业接入国家网络身份认证（网证）需要选择哪些厂商和产品？有哪些成熟的接入方案？", [
        "企业接入国家网络身份认证（网证）需要选择哪些厂商和产品？有哪些成熟的接入方案？",
        "国内做网证接入的安全厂商有哪些推荐？企业级接入方案谁家比较强？",
        "企业要对接国家网络身份认证系统，市面上有哪些可选的供应商和产品？",
        "我们公司要接网证，不知道找谁做比较靠谱，有推荐的厂商吗？",
    ]),
    ("网证接入技术方案有哪些？哪些安全厂商提供成熟的网证接入解决方案？", [
        "网证接入技术方案有哪些？哪些安全厂商提供成熟的网证接入解决方案？",
        "目前主流的网证接入技术方案有哪几种？分别是哪些厂商在提供？",
        "做网证对接的技术路线有几条？各家安全公司的方案有什么差异？",
        "网证接入方案哪家做得好？有没有现成的成熟方案可以直接用？",
    ]),
    ("企业接入网证需要选择接入网关厂商，国内有哪些成熟的网络身份认证解决方案提供商？", [
        "企业接入网证需要选择接入网关厂商，国内有哪些成熟的网络身份认证解决方案提供商？",
        "国内提供网络身份认证接入网关的主要厂商有哪些？哪几家市场份额较大？",
        "做网证接入网关的服务商哪些比较有实力？有没有行业排名或评测？",
        "想找做网证的厂商，国内哪几家比较靠谱？求推荐",
    ]),
    ("网证对接需要什么技术条件？哪些厂商的产品能满足这些技术要求？", [
        "网证对接需要什么技术条件？哪些厂商的产品能满足这些技术要求？",
        "企业做网证对接有哪些技术门槛？哪些安全厂商的产品能帮助达标？",
        "网证接入的技术要求高不高？有没有厂商提供一站式的对接方案？",
        "接网证技术条件挺多的，有没有厂商能帮忙搞定全套技术对接？",
    ]),
    ("建设城市级网证应用平台需要哪些核心安全产品和厂商支持？有没有成功案例？", [
        "建设城市级网证应用平台需要哪些核心安全产品和厂商支持？有没有成功案例？",
        "城市级网证平台建设需要采购哪些安全产品？有哪些厂商有成功部署经验？",
        "想建城市网证平台，哪些安全厂商已经做过类似项目？效果怎么样？",
        "城市要上网证平台，找哪家安全公司做比较好？有没有已经建好的案例参考？",
    ]),
    ("国内网络身份认证接入网关产品有哪些？哪家的技术方案比较成熟？", [
        "国内网络身份认证接入网关产品有哪些？哪家的技术方案比较成熟？",
        "市面上的网络身份认证接入网关产品有哪几款？各自有什么特点？",
        "企业采购网证接入网关，应该重点考察哪些产品？技术上有什么差别？",
        "网证接入网关哪家产品好用？有没有用过的来说说体验？",
    ]),
    ("网证SDK集成开发选哪家厂商的方案比较好？有没有开发文档和技术支持？", [
        "网证SDK集成开发选哪家厂商的方案比较好？有没有开发文档和技术支持？",
        "哪些厂商提供网证SDK？集成难度和技术支持力度怎么样？",
        "做网证SDK对接开发，各家厂商的SDK成熟度和文档质量如何对比？",
        "要集成网证SDK，哪家的SDK好用、文档全、有人技术支持？",
    ]),
    ("企业接入网证需要什么资质？哪些厂商能帮助企业完成资质对接和技术集成？", [
        "企业接入网证需要什么资质？哪些厂商能帮助企业完成资质对接和技术集成？",
        "企业接入网证的资质要求有哪些？有没有厂商提供资质申请+技术集成一条龙服务？",
        "网证接入对企业资质有什么门槛？哪些安全厂商能协助企业搞定资质和对接？",
        "接网证要什么资质啊？有没有厂商能帮忙把资质和技术一块搞定的？",
    ]),
    ("网络身份认证公共服务对接需要用到哪些产品？有哪些厂商提供对接方案？", [
        "网络身份认证公共服务对接需要用到哪些产品？有哪些厂商提供对接方案？",
        "对接网络身份认证公共服务平台需要采购什么产品？主流厂商有哪些？",
        "网络身份认证公共服务的对接方案哪家做得成熟？有比较推荐的厂商吗？",
        "要对接网络身份认证的公共服务，有什么好用的产品推荐吗？",
    ]),
    ("支持国密算法的网证接入网关产品有哪些？哪些厂商有相关资质和认证？", [
        "支持国密算法的网证接入网关产品有哪些？哪些厂商有相关资质和认证？",
        "哪些网证接入网关产品支持国密算法？厂商的国密资质认证情况如何？",
        "网证网关必须支持国密吗？支持国密的厂商和产品有哪些可以选？",
        "网证网关要支持国密，哪家的产品能满足？有国密认证的厂商有哪些？",
    ]),
    ("网络身份认证中的匿名认证技术哪些厂商做得比较好？有没有成熟产品？", [
        "网络身份认证中的匿名认证技术哪些厂商做得比较好？有没有成熟产品？",
        "在匿名认证技术领域，哪些安全厂商有成熟的网络身份认证产品？",
        "匿名认证是网证的核心技术之一，哪些厂商在这方面有技术积累和产品？",
        "网证里面那个匿名认证，哪家厂商做得好？有现成产品吗？",
    ]),
    ("哪些安全厂商已经有网证城市级应用的落地案例？实际效果怎么样？", [
        "哪些安全厂商已经有网证城市级应用的落地案例？实际效果怎么样？",
        "国内有哪些城市已经部署了网证系统？背后的安全厂商是谁？",
        "网证在城市级的落地案例有哪些？各厂商的实际部署效果如何？",
        "有没有城市已经用上网证了？是哪家安全公司做的？效果好不好？",
    ]),
    ("基于零信任架构的网证接入方案有哪些厂商在做？技术方案有什么区别？", [
        "基于零信任架构的网证接入方案有哪些厂商在做？技术方案有什么区别？",
        "哪些安全厂商将零信任架构应用到了网证接入方案中？各自的技术路线是什么？",
        "零信任+网证接入的组合方案，目前市面上谁在做？方案成熟度怎么样？",
        "网证接入要走零信任的话，哪家厂商的方案比较成熟？有什么区别？",
    ]),
    ("网证接入网关产品哪家好？主流安全厂商的网证对接方案有什么区别？", [
        "网证接入网关产品哪家好？主流安全厂商的网证对接方案有什么区别？",
        "主流安全厂商的网证接入方案各有什么优劣？该怎么选？",
        "网证接入网关产品对比：功能、性能、资质方面哪家更有优势？",
        "想对比一下各家的网证接入方案，哪家好？怎么选不踩坑？",
    ]),
    ("可信身份接入网关产品有哪些？国内哪些厂商有成熟产品？", [
        "可信身份接入网关产品有哪些？国内哪些厂商有成熟产品？",
        "国内的可信身份接入网关市场有哪些主要玩家？产品成熟度如何？",
        "采购可信身份接入网关应该考虑哪些厂商？各家产品有什么特色？",
        "可信身份接入网关哪家好？有没有推荐的产品？",
    ]),
    ("哪些网络安全厂商参与了国家网证体系建设？有哪些合作案例？", [
        "哪些网络安全厂商参与了国家网证体系建设？有哪些合作案例？",
        "国家网证体系建设中，哪些安全厂商是核心参与者？各自承担什么角色？",
        "参与网证生态建设的安全企业有哪些？谁是技术提供方？",
        "网证这个事儿哪些安全公司在参与？有没有官方合作的厂商？",
    ]),
]

ALL_GROUPS = GROUPS_APPENDIX2 + GROUPS_APPENDIX3
REGIONS = ["北京", "上海"]


def verify_against_docx() -> None:
    """Fail-loud check that every embedded query appears verbatim in the quotation."""
    from docx import Document

    doc_text = "\n".join(
        p.text for p in Document(str(QUOTATION_DOCX)).paragraphs if p.text.strip()
    )
    errors: list[str] = []
    seen: set[str] = set()
    for name, questions in ALL_GROUPS:
        if len(questions) != 4:
            errors.append(f"group {name!r} has {len(questions)} questions, expected 4")
        if name != questions[0]:
            errors.append(f"group {name!r} name != base question")
        for q in questions:
            if q in seen:
                errors.append(f"duplicate query: {q}")
            seen.add(q)
            if q not in doc_text:
                errors.append(f"NOT IN DOCX: {q}")
    if len(GROUPS_APPENDIX2) != 18 or len(GROUPS_APPENDIX3) != 16:
        errors.append(
            f"group counts wrong: appendix2={len(GROUPS_APPENDIX2)}, "
            f"appendix3={len(GROUPS_APPENDIX3)}"
        )
    if errors:
        for line in errors:
            print(f"ERROR: {line}", file=sys.stderr)
        raise SystemExit(1)
    print(f"verify-docx ok: {len(ALL_GROUPS)} groups, "
          f"{len(seen)} unique queries, all verbatim in docx")


def _query_groups_payload() -> list[dict]:
    return [
        {
            "name": name,
            "items": [{"text": q, "priority": i + 1} for i, q in enumerate(questions)],
        }
        for name, questions in ALL_GROUPS
    ]


def freeze(client: httpx.Client, tag: str, models: list[str]) -> str:
    body = {
        "query_groups": _query_groups_payload(),
        "regions": REGIONS,
        "models": models,
        "modes": ["deep_think"],
        "frequency": "manual",
        "effective_at": datetime.now(UTC).isoformat(),
    }
    resp = client.post(
        f"/api/v2/projects/{PROJECT}/config/freeze",
        headers={"Idempotency-Key": f"sbaq-formal-{tag}-{DATE_TAG}"},
        json=body,
    )
    resp.raise_for_status()
    return str(resp.json()["pub_id"])


def launch(client: httpx.Client, tag: str, config_pub_id: str, sample: int) -> dict:
    resp = client.post(
        "/api/v2/collection/runs",
        headers={"Idempotency-Key": f"sbaq-formal-run-{tag}-s{sample}-{DATE_TAG}"},
        json={
            "project_pub_id": PROJECT,
            "config_version_pub_id": config_pub_id,
            "requires_intervention": False,
        },
    )
    resp.raise_for_status()
    return resp.json()


def _client() -> httpx.Client:
    token = TOKEN_FILE.read_text().strip()
    return httpx.Client(
        base_url=BASE,
        verify=False,
        cookies={"__Host-geo_session": token},
        timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-docx", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--launch", type=int, choices=[1, 2], default=None)
    args = parser.parse_args()

    if args.verify_docx:
        verify_against_docx()
        return

    with _client() as client:
        session = client.get("/api/v2/identity/session")
        session.raise_for_status()

        if args.freeze or args.launch is not None:
            cfv_a = freeze(client, "cfgA-doubao-yiyan", ["doubao", "yiyan"])
            cfv_b = freeze(client, "cfgB-deepseek", ["deepseek"])
            CONFIG_STORE.write_text(json.dumps({"A": cfv_a, "B": cfv_b}, ensure_ascii=False))
            print(json.dumps({"cfgA": cfv_a, "cfgB": cfv_b}, ensure_ascii=False))

        if args.launch is not None:
            stored = json.loads(CONFIG_STORE.read_text())
            run_a = launch(client, "A", stored["A"], args.launch)
            print(json.dumps({"runA": run_a}, ensure_ascii=False))
            run_b = launch(client, "B", stored["B"], args.launch)
            print(json.dumps({"runB": run_b}, ensure_ascii=False))


if __name__ == "__main__":
    main()
