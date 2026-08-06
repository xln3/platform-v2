"""Intake（客户信息收集表）词表单一真源 + 三表模型。

词表口径照搬旧系统 server/geosys/intake/models.py（同事版 GEO-auto-analysis/user_form
固定选项）；portal 渲染与 API 校验都以此为唯一真源，固定选项一律 fail-closed 校验成员资格。
表口径：profile = project 1:1 可变草稿（upsert）；promo = 推广内容子表（payload 形状按 kind
校验）；trigger_question = 期望触发问法（draft → claim_created 后文本冻结）。
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..projects.models import TenantModel
from ..tenancy.database import Base

# ── 客户信息收集表词表（同事版 user_form 固定选项的单一真源）──────────────────────
INDUSTRIES = (
    "互联网 / 软件",
    "制造业",
    "金融",
    "教育培训",
    "医疗健康",
    "零售电商",
    "房地产",
    "汽车",
    "企业服务 / 咨询",
    "餐饮 / 消费",
    "其他",
)
GOALS = (
    "提升AI搜索曝光",
    "增加品牌被推荐频次",
    "获取销售线索",
    "建立行业权威形象",
    "纠正错误信息",
    "超越竞品曝光",
)
AUDIENCE_TYPES = ("B2B企业客户", "B2C个人消费者", "政府/机构", "经销商/渠道")
# 目标 AI 平台（客户想覆盖的平台意愿，绝不约束采集白名单）。
PLATFORMS = (
    "DeepSeek",
    "豆包",
    "文心一言",
    "通义千问",
    "Kimi",
    "腾讯元宝",
    "ChatGPT",
    "Claude",
    "Gemini",
)
PRODUCT_FEATURES = (
    "价格优势",
    "品质领先",
    "技术领先",
    "服务好",
    "交付快",
    "定制化",
    "安全可靠",
    "口碑好",
    "一站式",
)
COMPANY_STRENGTHS = (
    "团队规模领先",
    "多地服务网点",
    "资质认证齐全",
    "拥有专利",
    "行业协会成员",
    "知名合作伙伴",
    "获得融资",
    "上市公司",
)
PROMO_KINDS = frozenset({"product", "company"})
# 期望问法状态机：draft（可改可删）→ claim_created（文本冻结）。
TRIGGER_STATUS = frozenset({"draft", "claim_created"})

# ── 合同附件二《GEO 客户信息收集表（通用版）》合规栏目 ─────────────────────────
REVIEW_CATEGORIES = ("A", "B", "C", "D", "none")
REVIEW_CATEGORY_LABELS = {
    "A": "A类·法定前置审查（医疗/药品/医疗器械/保健食品/特医食品/农药/兽药）",
    "B": "B类·资质准入审查（金融/互联网金融/房地产/教育/电信/招商加盟/人力资源）",
    "C": "C类·内容合规审查（化妆品/医美/食品/酒类/旅游/养老等）",
    "D": "D类·禁止发布（烟草/处方药/特殊药品/婴儿乳制品替代母乳）",
    "none": "不属于上述分类",
}
AD_REVIEW_DOC_TYPES = (
    "医疗广告审查证明",
    "药品广告审查批准文号",
    "医疗器械广告审查批准文号",
    "保健食品广告审查批准文号",
    "特医食品广告审查批准文号",
    "农药广告审查批准文件",
    "兽药广告审查批准文件",
    "不适用（非A类行业）",
)
# 信息真实性确认（合同附件二原文五条，须全部勾选 → truth_confirmed=true；AI 永不代填）。
TRUTH_CONFIRM_ITEMS = (
    "本表所填信息及所附材料真实、准确、合法，且有相应文件支撑",
    "拟推广产品/服务属于可依法面向公众宣传的范围",
    "如属于法定前置审查行业（A类），已依法取得广告审查批准文件，且保证投放内容与审查批准内容一致，不擅自剪辑、拼接或修改已审查内容",
    "确认所属行业不属于法律禁止发布广告的行业（D类）",
    "已知悉：推广内容发布前将逐篇提交我司书面确认",
)

# intake_profile 可经 API 写的字段（API 名 == 列名；JSONB 列直接存数组/对象）
PROFILE_SCALAR_FIELDS = (
    "contact_person",
    "contact_info",
    "website",
    "wechat",
    "douyin",
    "social_media",
    "audience_desc",
    "business_license_code",
    "selling_points",
    "filler_name",
    "ad_review_no",
    "ad_review_authority",
    "ad_review_expiry",
)
# 词表标量：值必须 ∈ REVIEW_CATEGORIES（fail-closed），存短码。
PROFILE_VOCAB_SCALAR_FIELDS: dict[str, tuple[str, ...]] = {"review_category": REVIEW_CATEGORIES}
PROFILE_BOOL_FIELDS = ("pre_review_required", "truth_confirmed")
# 数组字段：API 名 → 成员词表（None = 自由文本）
PROFILE_LIST_FIELDS: dict[str, tuple[str, ...] | None] = {
    "goals": GOALS,
    "audience_type": AUDIENCE_TYPES,
    "platforms": PLATFORMS,
    "regions": None,
    "trademarks": None,
    "ad_review_doc_types": AD_REVIEW_DOC_TYPES,
    "evidence_links": None,
}
PROFILE_API_FIELDS = (
    set(PROFILE_SCALAR_FIELDS)
    | set(PROFILE_VOCAB_SCALAR_FIELDS)
    | set(PROFILE_BOOL_FIELDS)
    | set(PROFILE_LIST_FIELDS)
    | {"licenses"}
)

# promo payload 形状（fail-closed）：kind → (允许键, 必填键)
PROMO_PAYLOAD_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "product": (frozenset({"name", "category", "features", "desc", "price"}), frozenset({"name"})),
    "company": (frozenset({"name", "strength", "advantage", "cases", "data"}), frozenset()),
}


class IntakeProfile(TenantModel, Base):
    """客户信息收集表主表：每 project 一行可变草稿（upsert，部分更新）。"""

    __tablename__ = "intake_profile"
    __table_args__ = (UniqueConstraint("project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    contact_person: Mapped[str | None] = mapped_column(String(200))
    contact_info: Mapped[str | None] = mapped_column(String(500))
    website: Mapped[str | None] = mapped_column(String(500))
    wechat: Mapped[str | None] = mapped_column(String(200))
    douyin: Mapped[str | None] = mapped_column(String(200))
    social_media: Mapped[str | None] = mapped_column(Text)
    audience_desc: Mapped[str | None] = mapped_column(Text)
    business_license_code: Mapped[str | None] = mapped_column(String(18))
    selling_points: Mapped[str | None] = mapped_column(Text)
    filler_name: Mapped[str | None] = mapped_column(String(200))
    ad_review_no: Mapped[str | None] = mapped_column(String(200))
    ad_review_authority: Mapped[str | None] = mapped_column(String(200))
    ad_review_expiry: Mapped[str | None] = mapped_column(String(40))
    review_category: Mapped[str | None] = mapped_column(String(10))
    pre_review_required: Mapped[bool | None] = mapped_column(Boolean)
    truth_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    goals: Mapped[list[str]] = mapped_column(JSONB, default=list)
    audience_type: Mapped[list[str]] = mapped_column(JSONB, default=list)
    platforms: Mapped[list[str]] = mapped_column(JSONB, default=list)
    regions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    trademarks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ad_review_doc_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence_links: Mapped[list[str]] = mapped_column(JSONB, default=list)
    licenses: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    # 调研预填 provenance：API 字段名 → 来源（如 "research:ai-live"）；用户写即清标。
    prefilled: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class IntakePromo(TenantModel, Base):
    """推广内容子表：kind ∈ {product, company}，payload 形状按 kind fail-closed 校验。"""

    __tablename__ = "intake_promo"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class IntakeTriggerQuestion(TenantModel, Base):
    """期望触发问法：draft 可改可删；claim_created 后文本冻结（改问法 = 删了重录）。"""

    __tablename__ = "intake_trigger_question"
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "text"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("platform.project.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
