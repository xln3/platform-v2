"""Intake 请求/响应契约：词表 fail-closed 校验 + DLP（assert_secret_free）。

校验纪律（照搬旧 api.py 语义，落到 pydantic → 违规一律 422）：
  * 标量字符串：strip + ≤2000 字 + DLP；business_license_code 必须 [0-9A-Z]{18} fullmatch；
  * 词表字段：值必须 ∈ models 词表（fail-closed，不合法即拒，绝不静默丢弃）；
  * 数组字段：list[str]、≤100 项、单项 ≤500 字、（有词表的）成员资格 ⊆ 词表；
  * DLP 例外：contact_person/contact_info 合法持有手机号（DLP phone 规则会误伤），
    这两列不过 assert_secret_free——其余全部字符串字段（含数组元素）都过。
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from domain.evidence.dlp import assert_secret_free

from . import models

_LICENSE_CODE_RE = re.compile(r"[0-9A-Z]{18}")
_MAX_LIST_ITEMS = 100
_MAX_LIST_ELEMENT = 500
_MAX_SCALAR = 2000
_MAX_LICENSES = 20
_LICENSE_KEYS = frozenset({"name", "number", "expiry"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean_scalar(value: str | None, *, secret_free: bool = True) -> str | None:
    """标量清洗：strip、长度上限、DLP；空串归一为 None。"""
    if value is None:
        return None
    v = value.strip()
    if len(v) > _MAX_SCALAR:
        raise ValueError("too_long")
    if secret_free and v:
        assert_secret_free(v)
    return v or None


def _clean_list(value: list[str] | None, vocab: tuple[str, ...] | None) -> list[str] | None:
    """数组清洗：strip 去空、数量/长度上限、词表 fail-closed、逐项 DLP。"""
    if value is None:
        return None
    items = [x.strip() for x in value if x.strip()]
    if len(items) > _MAX_LIST_ITEMS:
        raise ValueError("too_many")
    if any(len(x) > _MAX_LIST_ELEMENT for x in items):
        raise ValueError("element_too_long")
    if vocab is not None:
        bad = [x for x in items if x not in vocab]
        if bad:
            raise ValueError(f"unknown_option:{bad[0]}")
    for x in items:
        assert_secret_free(x)
    return items


def _clean_licenses(value: list[dict[str, str]] | None) -> list[dict[str, str]] | None:
    """行业许可子表：[{name,number,expiry}]，仅编号/文本；全空行丢弃。"""
    if value is None:
        return None
    if len(value) > _MAX_LICENSES:
        raise ValueError("too_many")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or not set(item) <= _LICENSE_KEYS:
            raise ValueError("bad_item_keys")
        row = {k: (item.get(k) or "").strip() for k in ("name", "number", "expiry")}
        if any(len(x) > 200 for x in row.values()):
            raise ValueError("element_too_long")
        for x in row.values():
            if x:
                assert_secret_free(x)
        if any(row.values()):
            out.append(row)
    return out


class ProfileUpdate(StrictModel):
    """PUT /profile：部分更新——只写 body 里出现的字段（exclude_unset），未知字段 422。"""

    contact_person: str | None = None
    contact_info: str | None = None
    website: str | None = None
    wechat: str | None = None
    douyin: str | None = None
    social_media: str | None = None
    audience_desc: str | None = None
    business_license_code: str | None = None
    selling_points: str | None = None
    filler_name: str | None = None
    ad_review_no: str | None = None
    ad_review_authority: str | None = None
    ad_review_expiry: str | None = None
    review_category: str | None = None
    pre_review_required: bool | None = None
    truth_confirmed: bool | None = None
    goals: list[str] | None = None
    audience_type: list[str] | None = None
    platforms: list[str] | None = None
    regions: list[str] | None = None
    trademarks: list[str] | None = None
    ad_review_doc_types: list[str] | None = None
    evidence_links: list[str] | None = None
    licenses: list[dict[str, str]] | None = None

    @field_validator(
        "website",
        "wechat",
        "douyin",
        "social_media",
        "audience_desc",
        "selling_points",
        "filler_name",
        "ad_review_no",
        "ad_review_authority",
        "ad_review_expiry",
        mode="after",
    )
    @classmethod
    def clean_scalar(cls, value: str | None) -> str | None:
        return _clean_scalar(value)

    @field_validator("contact_person", "contact_info", mode="after")
    @classmethod
    def clean_contact(cls, value: str | None) -> str | None:
        # 联系人/联系方式合法持有手机号（DLP phone 规则误伤）——只 strip + 限长。
        return _clean_scalar(value, secret_free=False)

    @field_validator("business_license_code", mode="after")
    @classmethod
    def clean_license_code(cls, value: str | None) -> str | None:
        v = _clean_scalar(value)
        if v is not None and not _LICENSE_CODE_RE.fullmatch(v):
            raise ValueError("bad_format_expect_18_upper_alnum")
        return v

    @field_validator("review_category", mode="after")
    @classmethod
    def clean_review_category(cls, value: str | None) -> str | None:
        if value is not None and value not in models.REVIEW_CATEGORIES:
            raise ValueError("unknown_option")
        return value

    @field_validator(
        "goals",
        "audience_type",
        "platforms",
        "regions",
        "trademarks",
        "ad_review_doc_types",
        "evidence_links",
        mode="after",
    )
    @classmethod
    def clean_list(cls, value: list[str] | None, info: ValidationInfo) -> list[str] | None:
        return _clean_list(value, models.PROFILE_LIST_FIELDS[info.field_name or ""])

    @field_validator("licenses", mode="after")
    @classmethod
    def clean_licenses(cls, value: list[dict[str, str]] | None) -> list[dict[str, str]] | None:
        return _clean_licenses(value)


class ProfileView(StrictModel):
    project_pub_id: str
    exists: bool
    prefilled: dict[str, str]
    updated_at: str | None
    contact_person: str | None
    contact_info: str | None
    website: str | None
    wechat: str | None
    douyin: str | None
    social_media: str | None
    audience_desc: str | None
    business_license_code: str | None
    selling_points: str | None
    filler_name: str | None
    ad_review_no: str | None
    ad_review_authority: str | None
    ad_review_expiry: str | None
    review_category: str | None
    pre_review_required: bool | None
    truth_confirmed: bool | None
    goals: list[str]
    audience_type: list[str]
    platforms: list[str]
    regions: list[str]
    trademarks: list[str]
    ad_review_doc_types: list[str]
    evidence_links: list[str]
    licenses: list[dict[str, str]]


def validate_promo_payload(kind: str, payload: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """promo payload 形状 fail-closed 校验（照搬旧 _promo_payload）。

    未知键即拒；features/strength 成员资格 ⊆ 词表；字符串 ≤2000 字 + DLP；
    非 partial 时必填键（product.name）必须非空。"""
    allowed, required = models.PROMO_PAYLOAD_KEYS[kind]
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown_key:{sorted(unknown)[0]}")
    out: dict[str, Any] = {}
    for key in allowed:
        value = payload.get(key)
        if value is None:
            continue
        if key in ("features", "strength"):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"{key}_not_a_string_list")
            vocab = models.PRODUCT_FEATURES if key == "features" else models.COMPANY_STRENGTHS
            cleaned = _clean_list(value, vocab)
            out[key] = cleaned or []
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key}_not_a_string")
            if len(value) > _MAX_SCALAR:
                raise ValueError(f"{key}_too_long")
            assert_secret_free(value)
            out[key] = value.strip()
    if not partial:
        for key in required:
            if not out.get(key):
                raise ValueError(f"{key}_required")
    return out


class PromoCreate(StrictModel):
    kind: Literal["product", "company"]
    payload: dict[str, Any]

    @model_validator(mode="after")
    def check_payload(self) -> "PromoCreate":
        self.payload = validate_promo_payload(self.kind, self.payload, partial=False)
        return self


class PromoUpdate(StrictModel):
    kind: Literal["product", "company"] | None = None
    payload: dict[str, Any] | None = None


class PromoView(StrictModel):
    pub_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str


class PromoListView(StrictModel):
    items: list[PromoView]


class TriggerCreate(StrictModel):
    """批量收录：text 每行一条（她的表口径）。"""

    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text", mode="after")
    @classmethod
    def check_text(cls, value: str) -> str:
        assert_secret_free(value)
        return value

    def lines(self) -> list[str]:
        parts = [ln.strip() for ln in re.split(r"[\r\n]+", self.text) if ln.strip()]
        if not parts:
            raise ValueError("empty")
        if any(len(ln) > _MAX_LIST_ELEMENT for ln in parts):
            raise ValueError("line_too_long")
        return parts


class TriggerUpdate(StrictModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text", mode="after")
    @classmethod
    def check_text(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("empty")
        assert_secret_free(v)
        return v


class TriggerView(StrictModel):
    pub_id: str
    text: str
    status: str
    created_at: str


class TriggerCreateView(StrictModel):
    items: list[TriggerView]
    skipped_duplicates: list[str]


class TriggerListView(StrictModel):
    items: list[TriggerView]


class AiResearchRequest(StrictModel):
    brand: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)

    @field_validator("brand", mode="after")
    @classmethod
    def check_brand(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("brand_required")
        assert_secret_free(v)
        return v

    @field_validator("website", mode="after")
    @classmethod
    def check_website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        assert_secret_free(v)
        return v
