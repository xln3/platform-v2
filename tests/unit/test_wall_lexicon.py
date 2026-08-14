"""wall_lexicon 词表单测：事故原文命中 + 禁言 until 解析 + 正常答案零误伤回归。

词表是「配额/拒答/封禁」检测层的唯一真源（2026-08-13 豆包事故后建立）：
平台提示文案被当作答案采回时必须在验收门命中；正常中文答案（含「额度/
次数/禁言/会员」等词的第三人称句、条件句、科普句）必须一律 None。
"""

from __future__ import annotations

from datetime import datetime

from workflows.activities.wall_lexicon import (
    MUTED_PATTERNS,
    PLATFORMS,
    QUOTA_PHRASES,
    REFUSAL_PHRASES,
    WALL_LEXICON_VERSION,
    WallVerdict,
    classify_answer_text,
    detect_muted_banner,
)

# 2026-08-13 豆包事故原文（live 实证，逐字）。
_QUOTA_INCIDENT = (
    "今日专家模式免费次数用完了，暂时无法使用专业版功能，先使用快速模式和我聊聊"
    "别的吧。开通豆包专业版，免等待，继续为你服务。"
)
_MUTED_INCIDENT = (
    "由于违反用户使用规范，你的账号已被禁言至 2026 年 8 月 14 日 13:02，"
    "如有疑问请联系我们。"
)


def test_version_pinned() -> None:
    assert WALL_LEXICON_VERSION == "2026-08-14"


def test_tables_cover_all_five_platforms_plus_common() -> None:
    for table in (QUOTA_PHRASES, MUTED_PATTERNS, REFUSAL_PHRASES):
        assert "common" in table
        for platform in PLATFORMS:
            assert platform in table


def test_quota_incident_text_hits_wall_quota() -> None:
    verdict = classify_answer_text("doubao", _QUOTA_INCIDENT)
    assert verdict is not None
    assert verdict.wall_type == "wall_quota"
    assert verdict.phrase == "免费次数用完"
    assert verdict.until is None


def test_muted_incident_text_hits_wall_muted_with_until() -> None:
    verdict = classify_answer_text("doubao", _MUTED_INCIDENT)
    assert verdict is not None
    assert verdict.wall_type == "wall_muted"
    assert "已被禁言至" in verdict.phrase
    assert verdict.until == datetime(2026, 8, 14, 13, 2)


def test_muted_until_date_only_defaults_to_midnight() -> None:
    verdict = classify_answer_text("doubao", "你的账号已被禁言至 2026 年 8 月 14 日，请知悉。")
    assert verdict is not None
    assert verdict.wall_type == "wall_muted"
    assert verdict.until == datetime(2026, 8, 14, 0, 0)


def test_muted_without_date_still_hits_on_full_template() -> None:
    verdict = classify_answer_text(
        "doubao", "由于违反用户使用规范，你的账号已被禁言，如有疑问请联系我们。"
    )
    assert verdict is not None
    assert verdict.wall_type == "wall_muted"
    assert verdict.until is None


def test_quota_context_guard_requires_platform_voice() -> None:
    # 「免费次数用完」必须伴随「专家模式/专业版」语境——脱离语境不命中。
    assert classify_answer_text("doubao", "本活动的免费次数用完了，欢迎明天再来参与。") is None


def test_detect_muted_banner_on_page_text() -> None:
    page_text = f"豆包\n{_MUTED_INCIDENT}\n发送"
    verdict = detect_muted_banner("doubao", page_text)
    assert verdict is not None
    assert verdict.wall_type == "wall_muted"
    assert verdict.until == datetime(2026, 8, 14, 13, 2)


def test_detect_muted_banner_ignores_marketing_copy() -> None:
    # 整页 UI 含「开通会员」类营销件——banner 检测只跑禁言 regex，绝不套用
    # 配额/拒答词表（套了必误伤）。
    page_text = "豆包\n开通豆包专业版，免等待，继续为你服务。\n下载电脑版"
    assert detect_muted_banner("doubao", page_text) is None


def test_refusal_templates_hit() -> None:
    verdict = classify_answer_text("deepseek", "服务器繁忙，请稍后再试")
    assert verdict is not None and verdict.wall_type == "wall_refusal"
    verdict = classify_answer_text("doubao", "这个问题我们换个话题聊聊吧。")
    assert verdict is not None and verdict.wall_type == "wall_refusal"
    verdict = classify_answer_text("tongyi", "很抱歉，我暂时无法回答这个问题。")
    assert verdict is not None and verdict.wall_type == "wall_refusal"


def test_unknown_platform_falls_back_to_common_table() -> None:
    verdict = classify_answer_text("unknown_platform", "今日对话次数已达上限，请明天再试。")
    assert verdict is not None and verdict.wall_type == "wall_quota"
    assert classify_answer_text("unknown_platform", _MUTED_INCIDENT) is not None


def test_empty_text_never_hits() -> None:
    assert classify_answer_text("doubao", "") is None
    assert detect_muted_banner("doubao", "") is None


def test_verdict_is_frozen_dataclass() -> None:
    verdict = WallVerdict("wall_quota", "p")
    assert verdict.until is None
    try:
        verdict.phrase = "q"  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover - 防御冻结语义漂移
        raise AssertionError("WallVerdict must be frozen")


# ---------------------------------------------------------------------------
# 零误伤回归：正常中文答案（含「额度/次数/禁言/会员/封禁」等词）必须 None。
# 每条都是 GEO 采集里可能出现的合法答案句——词表任何改动若让下列句子命中，
# 就是误伤回退，必须加护栏而不是放行的。
# ---------------------------------------------------------------------------

_NORMAL_ANSWERS: tuple[str, ...] = (
    "这款重疾险的重疾保额额度最高可达五十万元，缴费次数灵活可选。",
    "会员每日签到的次数不限，开通会员可享受更多专属权益。",
    "如果你的账号已被禁言，可以通过客户端申诉入口提交材料。",
    "账号被封禁后，可以在七日内申请复核。",
    "群主有权将违规成员禁言，禁言时长可选。",
    "本次活动的免费抽奖次数已用完，次日零点恢复。",
    "会员每日可使用专家模式的次数不限。",
    "当前请求人数过多的问题可以通过水平扩容解决。",
    "建议避开高峰时段，今日对话次数较多的账号可能触发限流。",
    "DeepSeek 服务器繁忙时通常稍等片刻即可恢复使用。",
)


def test_normal_answers_never_classify_as_wall() -> None:
    for platform in PLATFORMS + ("unknown_platform",):
        for text in _NORMAL_ANSWERS:
            assert classify_answer_text(platform, text) is None, (platform, text)
            assert detect_muted_banner(platform, text) is None, (platform, text)
