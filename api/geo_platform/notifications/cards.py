from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .redaction import redact_notification_text
from .security import make_assist_capability

CARD_SCHEMA_VERSION = "1"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MD_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")
_TERMINAL_STATES = frozenset({"solved", "expired", "closed", "delivery_failed"})

_STATUS_LABELS = {
    "pending_delivery": "待投递",
    "active": "待处理",
    "claimed": "处理中",
    "solved": "已解决 / 已恢复",
    "expired": "已过期",
    "closed": "已关闭",
    "delivery_failed": "投递失败",
}
_HEADER_TEMPLATES = {
    "critical": "red",
    "warning": "orange",
    "info": "blue",
    "resolved": "green",
}


def clean_text(value: object, *, limit: int = 500, markdown: bool = False) -> str:
    text = _CONTROL_RE.sub("", redact_notification_text(value)).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return _MD_ESCAPE_RE.sub(r"\\\1", text) if markdown else text


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _remaining_ttl(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "-"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    seconds = max(0, int((aware - current).total_seconds()))
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}分{remainder}秒"


def _field(label: str, value: object) -> dict[str, Any]:
    return {
        "is_short": True,
        "text": {
            "tag": "lark_md",
            "content": f"**{clean_text(label, limit=40, markdown=True)}**\n"
            f"{clean_text(value, limit=240, markdown=True) or '-'}",
        },
    }


def _callback_button(
    notification_id: str,
    action: str,
    label: str,
    *,
    primary: bool = False,
) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": clean_text(label, limit=40)},
        "type": "primary" if primary else "default",
        "value": {
            "v": CARD_SCHEMA_VERSION,
            "notification_id": notification_id,
            "action": action,
        },
    }


def _base_card(*, title: str, severity: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    template = "green" if severity == "resolved" else _HEADER_TEMPLATES.get(severity, "blue")
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": clean_text(title, limit=100)},
        },
        "elements": elements,
    }


def build_assist_card(
    notice: Mapping[str, Any],
    *,
    public_base_url: str,
    link_signing_key: str,
    mention_oncall: bool = False,
    oncall_open_id: str = "",
) -> dict[str, Any]:
    summary_value = notice.get("summary")
    summary = summary_value if isinstance(summary_value, dict) else {}
    state = str(notice.get("state") or "pending_delivery")
    desired = str(notice.get("desired_state") or "active")
    display_state = desired if state == "pending_delivery" else state
    notification_id = str(notice["pub_id"])
    expires = notice.get("expires_at")
    ticket_sha256 = notice.get("assist_ticket_sha256")
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "fields": [
                _field("状态", _STATUS_LABELS.get(display_state, display_state)),
                _field("事件", summary.get("event_type", "人工接管")),
                _field("平台", summary.get("platform", "-")),
                _field("地域", summary.get("region", "-")),
                _field("账号", summary.get("account_mask", "未绑定")),
                _field(
                    "会话",
                    summary.get("session_public_id", notice.get("resource_pub_id", "-")),
                ),
                _field("创建", _format_time(notice.get("created_at"))),
                _field("到期", _format_time(expires if isinstance(expires, datetime) else None)),
                _field(
                    "剩余",
                    _remaining_ttl(expires if isinstance(expires, datetime) else None),
                ),
            ],
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**原因**\n"
                + clean_text(summary.get("reason", "-"), limit=500, markdown=True),
            },
        },
    ]
    if mention_oncall and oncall_open_id and display_state == "active":
        elements.insert(
            0,
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"<at id={oncall_open_id}></at> 请及时处理",
                },
            },
        )
    claimant = notice.get("claimed_actor_mask")
    if claimant:
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"处理人：{clean_text(claimant, limit=80)} · "
                        f"认领时间：{_format_time(notice.get('claimed_at'))}",
                    }
                ],
            }
        )

    if display_state not in _TERMINAL_STATES:
        actions: list[dict[str, Any]] = []
        if isinstance(ticket_sha256, str) and isinstance(expires, datetime) and public_base_url:
            expires_at = int(expires.timestamp())
            cap = make_assist_capability(
                notification_id=notification_id,
                ticket_sha256=ticket_sha256,
                expires_at=expires_at,
                key=link_signing_key,
            )
            actions.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "打开接管页"},
                    "type": "primary",
                    "url": f"{public_base_url.rstrip('/')}/api/v2/assist/notification/"
                    f"{notification_id}/{cap}",
                }
            )
        if display_state == "active":
            actions.append(_callback_button(notification_id, "claim", "我来处理"))
        elif display_state == "claimed":
            actions.append(_callback_button(notification_id, "release", "释放认领"))
        actions.append(_callback_button(notification_id, "recheck", "重新检测"))
        if display_state == "claimed":
            actions.append(_callback_button(notification_id, "complete", "确认完成"))
        elements.append({"tag": "action", "actions": actions})
    else:
        completed = notice.get("resolved_at") or notice.get("updated_at")
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "最终状态："
                        f"{_STATUS_LABELS.get(display_state, display_state)} · "
                        f"时间：{_format_time(completed)}",
                    }
                ],
            }
        )
    return _base_card(
        title=str(notice.get("title") or "GEO 人工接管"),
        severity=str(notice.get("severity") or "warning"),
        elements=elements,
    )


def build_alert_card(
    notice: Mapping[str, Any],
    *,
    mention_oncall: bool = False,
    oncall_open_id: str = "",
) -> dict[str, Any]:
    summary_value = notice.get("summary")
    summary = summary_value if isinstance(summary_value, dict) else {}
    state = str(notice.get("state") or "pending_delivery")
    desired = str(notice.get("desired_state") or "active")
    display_state = desired if state == "pending_delivery" else state
    resolved = display_state in {"solved", "closed"}
    fields = [
        _field("状态", "已恢复" if resolved else "告警中"),
        _field("级别", notice.get("severity", "unknown")),
        _field("告警", summary.get("alertname", "unknown")),
        _field("服务", summary.get("service", "-")),
        _field("类别", summary.get("category", "-")),
        _field("地域", summary.get("region", "-")),
        _field("首次", summary.get("starts_at", "-")),
        _field("最近", _format_time(notice.get("last_seen_at"))),
        _field("次数", notice.get("occurrence_count", 1)),
        _field("收口", _format_time(notice.get("resolved_at"))),
    ]
    elements: list[dict[str, Any]] = [
        {"tag": "div", "fields": fields},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**摘要**\n"
                + clean_text(summary.get("summary", "-"), limit=700, markdown=True),
            },
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "同一 fingerprint 的 firing/resolved 会更新本卡片；"
                    "机器人故障不会阻塞告警入口。",
                }
            ],
        },
    ]
    if (
        mention_oncall
        and oncall_open_id
        and not resolved
        and str(notice.get("severity")) == "critical"
    ):
        elements.insert(
            0,
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"<at id={oncall_open_id}></at> critical 告警",
                },
            },
        )
    severity = "resolved" if resolved else str(notice.get("severity") or "warning")
    return _base_card(
        title=str(notice.get("title") or "GEO 告警"), severity=severity, elements=elements
    )


def build_card(
    notice: Mapping[str, Any],
    *,
    public_base_url: str,
    link_signing_key: str,
    mention_oncall: bool = False,
    oncall_open_id: str = "",
) -> dict[str, Any]:
    if notice.get("kind") == "assist":
        return build_assist_card(
            notice,
            public_base_url=public_base_url,
            link_signing_key=link_signing_key,
            mention_oncall=mention_oncall,
            oncall_open_id=oncall_open_id,
        )
    return build_alert_card(
        notice,
        mention_oncall=mention_oncall,
        oncall_open_id=oncall_open_id,
    )
