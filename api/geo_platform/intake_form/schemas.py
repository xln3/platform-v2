"""Intake form（免登录填表通道）请求/响应契约：DLP + 长度 fail-closed。

词表类字段的 fail-closed 校验复用 intake/schemas（本模块不再另起词表）；
这里只覆盖 invite 签发、brand/competitor、AI 扩写与 SiliconIndex 预览的出入参。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.evidence.dlp import assert_secret_free


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean(value: str, *, max_len: int) -> str:
    v = value.strip()
    if not v:
        raise ValueError("empty")
    if len(v) > max_len:
        raise ValueError("too_long")
    assert_secret_free(v)
    return v


def _clean_opt(value: str | None, *, max_len: int) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > max_len:
        raise ValueError("too_long")
    assert_secret_free(v)
    return v


# ── 邀请签发/管理（运营端，intake:write）────────────────────────────────────
class InviteCreate(StrictModel):
    """缺省值走 config（intake_invite_ttl_hours / intake_invite_ai_quota）；可显式覆盖。"""

    ttl_hours: int | None = Field(default=None, ge=1, le=720)
    ai_quota: int | None = Field(default=None, ge=0, le=100)


class InviteView(StrictModel):
    pub_id: str
    project_pub_id: str
    expires_at: str
    revoked_at: str | None
    submitted_at: str | None
    ai_quota: int
    ai_used: int
    created_by: str
    created_at: str


class InviteListView(StrictModel):
    items: list[InviteView]


class InviteCreatedView(InviteView):
    """token 原文只在签发响应出现这一次（库存 sha256）。"""

    token: str


# ── brand / competitor ─────────────────────────────────────────────────────
class BrandUpdate(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    aliases: list[str] | None = None

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _clean_opt(value, max_len=200)

    @field_validator("website", mode="after")
    @classmethod
    def clean_website(cls, value: str | None) -> str | None:
        return _clean_opt(value, max_len=500)

    @field_validator("aliases", mode="after")
    @classmethod
    def clean_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > 20:
            raise ValueError("too_many")
        return [x for x in (_clean_opt(v, max_len=200) for v in value) if x]


class BrandView(StrictModel):
    exists: bool
    pub_id: str | None
    name: str | None
    website: str | None
    aliases: list[str]


class CompetitorCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=500)

    @field_validator("name", mode="after")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean(value, max_len=200)

    @field_validator("website", mode="after")
    @classmethod
    def clean_website(cls, value: str | None) -> str | None:
        return _clean_opt(value, max_len=500)


class CompetitorView(StrictModel):
    pub_id: str
    name: str
    website: str | None
    created_at: str


class CompetitorListView(StrictModel):
    items: list[CompetitorView]


# ── AI 扩写问法 / SiliconIndex 预览 ─────────────────────────────────────────
class QuerySuggestionsRequest(StrictModel):
    core_words: list[str] = Field(min_length=1, max_length=20)
    n: int = Field(default=12, ge=1, le=50)

    @field_validator("core_words", mode="after")
    @classmethod
    def clean_words(cls, value: list[str]) -> list[str]:
        out = [x for x in (_clean_opt(v, max_len=100) for v in value) if x]
        if not out:
            raise ValueError("core_words_required")
        return out


class TemplateQuestionsRequest(StrictModel):
    region: str = Field(default="", max_length=100)
    competitor: str = Field(default="", max_length=200)

    @field_validator("region", mode="after")
    @classmethod
    def clean_region(cls, value: str) -> str:
        v = value.strip()
        if v:
            assert_secret_free(v)
        return v

    @field_validator("competitor", mode="after")
    @classmethod
    def clean_competitor(cls, value: str) -> str:
        v = value.strip()
        if v:
            assert_secret_free(v)
        return v
