"""报价单服务目录与套餐定义。

服务目录是后端校验与报价单正文的权威来源。套餐只预选服务，不携带价格；每项服务的价格由
运营人员针对当前客户单独录入，总价由后端按选中服务逐项求和。前端为即时展示保留镜像定义，
发布时必须通过契约测试同步校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ServiceCode = Literal[
    "ranking_test",
    "outbound_disparagement_audit",
    "inbound_disparagement_audit",
    "official_site_audit",
    "content_publishing_pilot",
]
PackageCode = Literal["geo_effect_assessment", "minimum_validation", "custom"]


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    number: int
    code: ServiceCode
    short_name: str
    name: str
    unit: str
    summary: str
    scope: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PackageDefinition:
    code: PackageCode
    name: str
    audience: str
    summary: str
    execution_sequence: tuple[ServiceCode, ...]
    service_quantities: tuple[tuple[ServiceCode, int], ...]
    conditional_service_codes: tuple[ServiceCode, ...] = ()
    preconditions: tuple[str, ...] = ()
    handoffs: tuple[str, ...] = ()

    @property
    def service_codes(self) -> tuple[ServiceCode, ...]:
        return tuple(code for code, _ in self.service_quantities)


SERVICE_CATALOG: tuple[ServiceDefinition, ...] = (
    ServiceDefinition(
        number=1,
        code="ranking_test",
        short_name="测试",
        name="AI 推荐排名效果测试",
        unit="轮",
        summary=(
            "用同一组真实业务问题分别观察模型 API 与豆包 App 等消费者端结果，"
            "评估品牌是否被提及、推荐以及排在什么位置。"
        ),
        scope=(
            "围绕双方冻结的核心业务问题集构建语义变体，并按约定平台、地域和重复次数采样",
            "把开放 API 与豆包 App 作为两个独立观测渠道，不把网页结果改名为 App 结果",
            "统计品牌提及率、推荐排名分布、Top1/Top3/Top5 出现率及竞品差异",
        ),
        inputs=(
            "客户/品牌名称、产品与业务方向",
            "核心业务问题或目标词，可通过 XLSX 提供",
            "主要竞争对手、目标地域、同题同窗重复次数",
            "API 提供方/模型/版本与豆包 App 版本、账号、地域等采集通道信息",
        ),
        outputs=(
            "API 与豆包 App 逐题对照证据",
            "品牌提及、推荐排名及竞品对比评测报告",
            "接口结果与手机端观测差异说明，不预设差异方向或推断平台算法根因",
        ),
    ),
    ServiceDefinition(
        number=2,
        code="outbound_disparagement_audit",
        short_name="找拉踩帖",
        name="主动拉踩内容核查",
        unit="项",
        summary=(
            "检查有作者、委托或审批等归属证据的己方已投/拟投内容，是否通过贬低或不实比较拉踩竞品，"
            "降低内容合规和舆情风险。"
        ),
        scope=(
            "定位同时出现品牌与竞品的比较内容和来源页面",
            "识别贬低性措辞、不对称比较和缺少依据的事实陈述",
            "对候选内容逐条保留原文、页面和事实核查依据",
        ),
        inputs=(
            "品牌别名、产品名与主要竞品名单",
            "已投/拟投内容 URL 或稿件，以及作者、委托或审批等己方归属证据",
            "客户确认的产品事实与合规材料",
        ),
        outputs=(
            "疑似拉踩帖子清单与风险分级",
            "原文片段、页面位置、URL 与截图证据",
            "逐条事实核查结论及修改建议",
        ),
    ),
    ServiceDefinition(
        number=3,
        code="inbound_disparagement_audit",
        short_name="找被拉踩帖",
        name="被拉踩内容核查",
        unit="项",
        summary=(
            "从 AI 回答及其第三方信源中查找贬低客户品牌、夸大竞品优势或传播不实比较的内容，"
            "识别疑似竞争性 GEO 线索。"
        ),
        scope=(
            "沿 AI 回答引用与检索结果查找涉及客户品牌的负向比较内容",
            "区分可核验的客观比较、意见表达和疑似不实拉踩",
            "记录内容影响的查询、AI 回答和潜在竞争对手线索",
        ),
        inputs=(
            "客户品牌、产品别名和潜在竞争对手",
            "服务 1 产出的 AI 回答与引用池；未选服务 1 时由客户提供目标问题和来源 URL",
            "客户确认的产品事实、资质和案例材料",
        ),
        outputs=(
            "被拉踩帖子及传播来源清单",
            "负向表述、引用关系与页面证据",
            "事实核查、影响范围和处置优先级；不在证据不足时归因具体竞品投放",
        ),
    ),
    ServiceDefinition(
        number=4,
        code="official_site_audit",
        short_name="官网分析",
        name="官网内容 AI 引用效率分析",
        unit="项",
        summary=(
            "识别 AI 回答引用 URL 中的官网页面，评估官网内容的引用与回答级采纳证据，"
            "并定位影响 AI 使用效率的内容问题。"
        ),
        scope=(
            "按客户确认的官网域名识别 AI 回答中的官网引用",
            "分析官网引用率、内容采纳率及引用片段是否准确",
            "定位页面结构、内容表达和事实支撑方面的问题",
        ),
        inputs=(
            "客户确认的官网 URL 与可纳入分析的子域名",
            "服务 1 产出的 AI 回答和全部引用 URL；未选服务 1 时由客户导入",
            "官网页面快照或允许采集的页面范围",
        ),
        outputs=(
            "官网引用率与内容采纳率分析；证据不足时明确交付证据不足结论",
            "被引用页面、引用片段和回答关系证据",
            "官网内容问题清单及可执行优化建议",
        ),
    ),
    ServiceDefinition(
        number=5,
        code="content_publishing_pilot",
        short_name="发帖提排名",
        name="内容发布与排名提升试点",
        unit="项",
        summary=(
            "围绕少量已确认问题发布合规内容，并在相同采样条件下比较发布前后结果，"
            "验证内容建设能否提升品牌提及、引用和推荐排名。"
        ),
        scope=(
            "选择小规模目标 Query，制定内容与信源发布方案",
            "完成约定媒体发布并保存公开 URL、发布时间和内容证据",
            "发帖完成后由服务 1 的第二轮复测按相同口径检验结果",
        ),
        inputs=(
            "目标 Query、期望提升方向与品牌事实材料",
            "经客户确认的稿件、媒体范围、预算与发布授权",
            "服务 1 首轮产出的基线快照，以及同一服务第二轮所需的一致采样配置",
        ),
        outputs=(
            "试点方案、发布清单与可访问 URL",
            "基于服务 1 两轮采集指标的发布试点解释，不重复收取测试费用",
            "试点结论、证据边界与下一阶段建议；不承诺一定提升",
        ),
    ),
)

SERVICE_BY_CODE = {service.code: service for service in SERVICE_CATALOG}

PACKAGE_CATALOG: tuple[PackageDefinition, ...] = (
    PackageDefinition(
        code="geo_effect_assessment",
        name="已开展 GEO · 效果评测",
        audience="已经开展过 GEO 的公司",
        summary="评测现有排名效果、双向拉踩风险与官网内容的 AI 使用效率。",
        execution_sequence=(
            "ranking_test",
            "outbound_disparagement_audit",
            "inbound_disparagement_audit",
            "official_site_audit",
        ),
        service_quantities=(
            ("ranking_test", 1),
            ("outbound_disparagement_audit", 1),
            ("inbound_disparagement_audit", 1),
            ("official_site_audit", 1),
        ),
        preconditions=(
            "服务 1 使用客户已有 GEO 目标问题，由客户确认后冻结测试集。",
            "服务 2 只纳入具有作者、委托或审批等归属证据的己方内容。",
        ),
        handoffs=("服务 1 产出回答、排名和引用池，服务 3 和 4 复用该证据集。",),
    ),
    PackageDefinition(
        code="minimum_validation",
        name="未开展 GEO · 最小化验证",
        audience="尚未开展 GEO、希望先用最小投入验证价值的公司",
        summary="验证推荐潜力、被竞品拉踩风险、官网引用效率和小规模发帖后的排名变化。",
        execution_sequence=(
            "ranking_test",
            "inbound_disparagement_audit",
            "official_site_audit",
            "content_publishing_pilot",
            "ranking_test",
        ),
        service_quantities=(
            ("ranking_test", 2),
            ("inbound_disparagement_audit", 1),
            ("official_site_audit", 1),
            ("content_publishing_pilot", 1),
        ),
        conditional_service_codes=("official_site_audit",),
        preconditions=(
            "首轮服务 1 之前，我方提出小规模候选问题，客户确认并冻结测试集。",
            "服务 4 只在引用 URL 已确认命中客户官网时纳入价格。",
        ),
        handoffs=(
            "首轮服务 1 产出回答、排名、引用池和基线快照，服务 3、4、5 分别复用。",
            "服务 1 负责两轮采集与指标计算；服务 5 负责发布、发布证据和基于服务 1 结果的试点解释。",
        ),
    ),
    PackageDefinition(
        code="custom",
        name="自定义组合",
        audience="只需要部分服务或需另行组合的客户",
        summary="从五项原子服务中自由选择；每项服务仍单独报价。",
        execution_sequence=(),
        service_quantities=(),
    ),
)

PACKAGE_BY_CODE = {package.code: package for package in PACKAGE_CATALOG}


def ordered_service_codes(
    codes: set[ServiceCode] | frozenset[ServiceCode],
) -> tuple[ServiceCode, ...]:
    """按业务编号返回稳定顺序，避免输入顺序改变报价单版式。"""
    return tuple(service.code for service in SERVICE_CATALOG if service.code in codes)
