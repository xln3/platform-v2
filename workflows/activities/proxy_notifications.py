"""Proxy purchase notices use the existing durable Feishu delivery queue."""

from datetime import datetime

import structlog
from geo_platform.notifications.config import FeishuBotConfig
from geo_platform.notifications.models import Notice
from geo_platform.notifications.service import NotificationService
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import select

from workflows.activities.elastic_proxy import PURCHASE_TIMEZONE

log = structlog.get_logger()


def notify_proxy_event(region: str, now: datetime, *, event: str, summary: str) -> bool:
    config = FeishuBotConfig.from_env()
    if not config.chat_id:
        return False
    day = now.astimezone(PURCHASE_TIMEZONE).date().isoformat()
    try:
        with SessionLocal() as session:
            result = NotificationService(session).record_alert(
                {
                    "alertname": "GeoProxyPurchase",
                    "status": "firing",
                    "severity": "warning",
                    "category": "capacity",
                    "service": "elastic-proxies",
                    "region": region,
                    "summary": summary,
                    "fingerprint": f"proxy:{region}:{day}:{event}",
                },
                target_chat_id=config.chat_id,
                repeat_window_seconds=86400,
                card_update_seconds=86400,
            )
            notice = session.scalar(select(Notice).where(Notice.pub_id == result.notification_id))
            delivered = bool(notice and notice.message_id)
            session.commit()
            return delivered
    except Exception as exc:
        log.warning("proxy_notification_failed", marker=type(exc).__name__, region=region)
        return False


def notify_purchase_needed(region: str, now: datetime) -> bool:
    return notify_proxy_event(
        region,
        now,
        event="needed",
        summary=(
            f"悟空代理地域 {region} 暂无可用租约，需要检查并补充容量。"
            "已有有效订单会复用，失联但未到期的订单不会重复购买。"
            "每日自动购买硬上限 3 个 IP（日本时间零点重置）；"
            "收到本通知的发送回执后才允许尝试付费购买。"
        ),
    )
