from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from geo_platform.collection import relay_probe
from geo_platform.notifications.config import FeishuBotConfig
from geo_platform.notifications.feishu_client import FeishuApiError, FeishuResult
from geo_platform.notifications.interactions import InteractionService
from geo_platform.notifications.models import DeliveryCommand, Interaction, Notice
from geo_platform.notifications.security import actor_hash
from geo_platform.notifications.sender import NotificationSender
from geo_platform.notifications.service import NotificationService
from geo_platform.tenancy.database import SessionLocal
from sqlalchemy import func, select, text


def _registry(directory: Path, *, session_kind: str = "otp_cli") -> tuple[str, str]:
    ticket = "test-ticket-" + uuid.uuid4().hex
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    now = int(datetime.now(UTC).timestamp())
    record = {
        "version": 1,
        "session_kind": session_kind,
        "run_pub_id": f"otp-assist-test-{uuid.uuid4().hex}",
        "session_id": f"session-{uuid.uuid4().hex}",
        "ticket_hash": digest,
        "port": 19226,
        "platform": "yiyan",
        "instance_key": "yiyan_sh",
        "state": "active",
        "business_key": "integration test",
        "evidence_ref": None,
        "created_at": now,
        "expires_at": now + 900,
        "push_sent": True,
        "solved_at": None,
    }
    (directory / f"{digest}.json").write_text(json.dumps(record), encoding="utf-8")
    return digest, record["session_id"]


def _seed_assist(directory: Path) -> Notice:
    digest, _session_id = _registry(directory)
    now = datetime.now(UTC)
    notice = Notice(
        pub_id="ntf_" + uuid.uuid4().hex[:26],
        kind="assist",
        channel="feishu_app",
        fingerprint=uuid.uuid4().hex,
        state="active",
        desired_state="active",
        severity="warning",
        title="assist",
        summary={"platform": "yiyan"},
        target_chat_id="oc_test",
        message_id="om_" + uuid.uuid4().hex,
        session_kind="otp_cli",
        resource_pub_id="otp-assist-test",
        assist_ticket_sha256=digest,
        occurrence_count=1,
        revision=1,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=15),
        created_at=now,
        updated_at=now,
    )
    with SessionLocal() as session:
        session.add(notice)
        session.commit()
    return notice


def _payload(
    notice: Notice,
    *,
    actor: str,
    action: str,
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id or "evt_" + uuid.uuid4().hex,
            "event_type": "card.action.trigger",
            "app_id": "cli_test",
            "tenant_key": "tenant_test",
            "token": "verification-test",
        },
        "event": {
            "operator": {"open_id": actor, "tenant_key": "tenant_test"},
            "action": {
                "value": {
                    "v": "1",
                    "notification_id": notice.pub_id,
                    "action": action,
                }
            },
            "context": {
                "open_message_id": notice.message_id,
                "open_chat_id": notice.target_chat_id,
            },
        },
    }


def _handle(
    notice: Notice,
    directory: Path,
    *,
    actor: str,
    action: str,
    event_id: str | None = None,
    replay_key: str | None = None,
    allowed: frozenset[str] | None = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        result = InteractionService(
            session,
            app_id="cli_test",
            tenant_key="tenant_test",
            allowed_open_ids=allowed or frozenset({actor}),
            replay_ttl_seconds=300,
            registry_dir=directory,
        ).handle(
            _payload(notice, actor=actor, action=action, event_id=event_id),
            replay_key=replay_key or uuid.uuid4().hex,
        )
        session.commit()
        return result.response


def test_two_people_claim_concurrently_only_one_wins(tmp_path: Path) -> None:
    notice = _seed_assist(tmp_path)
    actors = ("ou_operator_alpha", "ou_operator_bravo")
    barrier = threading.Barrier(2)

    def claim(actor: str) -> str:
        barrier.wait(timeout=5)
        response = _handle(
            notice,
            tmp_path,
            actor=actor,
            action="claim",
            allowed=frozenset(actors),
        )
        return str(response["toast"]["content"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, actors))
    assert sorted(results) == ["已有其他值班人员认领", "认领成功"]
    with SessionLocal() as session:
        saved = session.scalar(select(Notice).where(Notice.pub_id == notice.pub_id))
        assert saved is not None
        assert saved.desired_state == "claimed"
        assert saved.claimed_actor_hash in {actor_hash(actor) for actor in actors}


def test_concurrent_assist_enqueue_is_idempotent() -> None:
    session_id = "session-" + uuid.uuid4().hex
    ticket_sha256 = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    created_at = int(datetime.now(UTC).timestamp())
    barrier = threading.Barrier(2)

    def enqueue() -> str:
        with SessionLocal() as session:
            barrier.wait(timeout=5)
            notification_id = NotificationService(session).enqueue_assist(
                tenant_pub_id=None,
                session_kind="otp_cli",
                run_pub_id="otp-assist-concurrent",
                session_id=session_id,
                ticket_sha256=ticket_sha256,
                platform="yiyan",
                instance_key="yiyan_sh",
                business_key="concurrent enqueue",
                created_at_epoch=created_at,
                expires_at_epoch=created_at + 900,
                target_chat_id="oc_test",
            )
            session.commit()
            return notification_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        notification_ids = list(executor.map(lambda _index: enqueue(), range(2)))
    assert len(set(notification_ids)) == 1
    with SessionLocal() as session:
        notice = session.scalar(select(Notice).where(Notice.pub_id == notification_ids[0]))
        assert notice is not None
        command_count = session.scalar(
            select(func.count())
            .select_from(DeliveryCommand)
            .where(DeliveryCommand.notice_id == notice.id)
        )
        assert command_count == 1


def test_event_replay_is_idempotent_and_release_requires_claimant(tmp_path: Path) -> None:
    notice = _seed_assist(tmp_path)
    actor = "ou_operator_alpha"
    event_id = "evt_" + uuid.uuid4().hex
    replay_key = uuid.uuid4().hex
    first = _handle(
        notice,
        tmp_path,
        actor=actor,
        action="claim",
        event_id=event_id,
        replay_key=replay_key,
    )
    second = _handle(
        notice,
        tmp_path,
        actor=actor,
        action="claim",
        event_id=event_id,
        replay_key=replay_key,
    )
    assert first == second
    repeated_claim = _handle(notice, tmp_path, actor=actor, action="claim")
    assert repeated_claim["toast"]["content"] == "你已认领，无需重复操作"
    denied = _handle(
        notice,
        tmp_path,
        actor="ou_operator_bravo",
        action="release",
        allowed=frozenset({actor, "ou_operator_bravo"}),
    )
    assert denied["toast"]["content"] == "只有当前认领人可以释放"
    released = _handle(notice, tmp_path, actor=actor, action="release")
    assert released["toast"]["content"] == "已释放认领"
    with SessionLocal() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Interaction)
                .where(Interaction.event_id == event_id)
            )
            == 1
        )


def test_non_allowlisted_actor_is_audited_and_does_not_claim(tmp_path: Path) -> None:
    notice = _seed_assist(tmp_path)
    response = _handle(
        notice,
        tmp_path,
        actor="ou_not_allowed",
        action="claim",
        allowed=frozenset({"ou_someone_else"}),
    )
    assert response["toast"]["content"] == "你没有执行此操作的权限"
    with SessionLocal() as session:
        saved = session.scalar(select(Notice).where(Notice.pub_id == notice.pub_id))
        assert saved is not None and saved.desired_state == "active"
        interaction = session.scalar(select(Interaction).where(Interaction.notice_id == saved.id))
        assert interaction is not None and interaction.result == "forbidden"
        assert interaction.actor_hash == actor_hash("ou_not_allowed")


def test_terminal_and_expired_notices_reject_actions(tmp_path: Path) -> None:
    terminal = _seed_assist(tmp_path)
    with SessionLocal() as session:
        notice = session.scalar(select(Notice).where(Notice.pub_id == terminal.pub_id))
        assert notice is not None
        notice.state = "solved"
        notice.desired_state = "solved"
        notice.resolved_at = datetime.now(UTC)
        session.commit()
    response = _handle(terminal, tmp_path, actor="ou_operator_alpha", action="claim")
    assert response["toast"]["content"] == "接管会话已结束"
    with SessionLocal() as session:
        NotificationService(session).mark_assist_state_by_ticket(
            ticket_sha256=str(terminal.assist_ticket_sha256),
            state="closed",
        )
        session.commit()
    with SessionLocal() as session:
        saved_terminal = session.scalar(select(Notice).where(Notice.pub_id == terminal.pub_id))
        assert saved_terminal is not None and saved_terminal.desired_state == "solved"

    expired = _seed_assist(tmp_path)
    with SessionLocal() as session:
        notice = session.scalar(select(Notice).where(Notice.pub_id == expired.pub_id))
        assert notice is not None
        notice.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    response = _handle(expired, tmp_path, actor="ou_operator_alpha", action="claim")
    assert response["toast"]["content"] == "接管会话已过期"
    with SessionLocal() as session:
        saved = session.scalar(select(Notice).where(Notice.pub_id == expired.pub_id))
        assert saved is not None and saved.desired_state == "expired"


def test_otp_complete_marks_notice_without_temporal_signal(tmp_path: Path) -> None:
    notice = _seed_assist(tmp_path)
    actor = "ou_operator_alpha"
    _handle(notice, tmp_path, actor=actor, action="claim")
    response = _handle(notice, tmp_path, actor=actor, action="complete")
    assert response["toast"]["content"] == "已确认完成"
    with SessionLocal() as session:
        saved = session.scalar(select(Notice).where(Notice.pub_id == notice.pub_id))
        assert saved is not None and saved.desired_state == "solved"
        signal_count = session.execute(
            text(
                "SELECT count(*) FROM integration.workflow_signal_command "
                "WHERE args @> CAST(:needle AS jsonb)"
            ),
            {"needle": json.dumps(["otp-assist-test"])},
        ).scalar_one()
        assert signal_count == 0


def test_alert_repeat_and_resolved_update_one_notice_across_sessions() -> None:
    fingerprint = "alert-" + uuid.uuid4().hex
    alert = {
        "status": "firing",
        "fingerprint": fingerprint,
        "alertname": "GeoCollectionRunStalled",
        "severity": "warning",
        "service": "collection-worker",
        "summary": "stalled",
    }
    with SessionLocal() as session:
        first = NotificationService(session).record_alert(
            alert,
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    with SessionLocal() as session:
        notice = session.scalar(select(Notice).where(Notice.pub_id == first.notification_id))
        assert notice is not None
        notice.message_id = "om_alert"
        notice.state = "active"
        session.commit()
    with SessionLocal() as session:
        repeated = NotificationService(session).record_alert(
            alert,
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    with SessionLocal() as session:
        resolved = NotificationService(session).record_alert(
            {**alert, "status": "resolved"},
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    assert repeated.notification_id == resolved.notification_id == first.notification_id
    assert repeated.delivery_enqueued is False
    assert resolved.transition is True and resolved.delivery_enqueued is True
    with SessionLocal() as session:
        notices = list(
            session.scalars(
                select(Notice).where(Notice.kind == "alert", Notice.fingerprint == fingerprint)
            )
        )
        assert len(notices) == 1
        assert notices[0].desired_state == "solved"
        assert notices[0].occurrence_count == 3
        commands = list(
            session.scalars(
                select(DeliveryCommand)
                .where(DeliveryCommand.notice_id == notices[0].id)
                .order_by(DeliveryCommand.id)
            )
        )
        assert [(command.operation, command.notice_revision) for command in commands] == [
            ("send", 1),
            ("update", 2),
        ]


def test_alert_without_provider_fingerprint_still_closes_before_initial_send() -> None:
    alertname = "SyntheticNoFingerprint" + uuid.uuid4().hex
    firing = {
        "status": "firing",
        "alertname": alertname,
        "severity": "critical",
        "service": "collection-worker",
        "starts_at": "2026-08-14T08:00:00Z",
        "summary": "first summary",
    }
    with SessionLocal() as session:
        first = NotificationService(session).record_alert(
            firing,
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    with SessionLocal() as session:
        resolved = NotificationService(session).record_alert(
            {
                **firing,
                "status": "resolved",
                "summary": "changed summary",
                "ends_at": "2026-08-14T08:01:00Z",
            },
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    assert resolved.notification_id == first.notification_id
    with SessionLocal() as session:
        notice = session.scalar(select(Notice).where(Notice.pub_id == first.notification_id))
        assert notice is not None
        assert notice.desired_state == "solved" and notice.occurrence_count == 2
        commands = list(
            session.scalars(select(DeliveryCommand).where(DeliveryCommand.notice_id == notice.id))
        )
        assert [(command.operation, command.state) for command in commands] == [("send", "pending")]


def test_concurrent_first_alert_delivery_creates_one_notice() -> None:
    fingerprint = "alert-concurrent-" + uuid.uuid4().hex
    alert = {
        "status": "firing",
        "fingerprint": fingerprint,
        "alertname": "GeoCollectionRunStalled",
        "severity": "warning",
        "service": "collection-worker",
        "summary": "stalled",
    }
    barrier = threading.Barrier(2)

    def record() -> tuple[bool, bool, str]:
        with SessionLocal() as session:
            barrier.wait(timeout=5)
            result = NotificationService(session).record_alert(
                alert,
                target_chat_id="oc_test",
                repeat_window_seconds=14_400,
                card_update_seconds=900,
            )
            session.commit()
            return result.created, result.delivery_enqueued, result.notification_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: record(), range(2)))
    assert sorted(created for created, _queued, _notice_id in results) == [False, True]
    assert sum(queued for _created, queued, _notice_id in results) == 1
    assert len({notice_id for _created, _queued, notice_id in results}) == 1
    with SessionLocal() as session:
        notices = list(
            session.scalars(
                select(Notice).where(Notice.kind == "alert", Notice.fingerprint == fingerprint)
            )
        )
        assert len(notices) == 1
        assert notices[0].occurrence_count == 2


def test_orphan_resolved_alert_does_not_create_green_card() -> None:
    with SessionLocal() as session:
        result = NotificationService(session).record_alert(
            {
                "status": "resolved",
                "fingerprint": "orphan-" + uuid.uuid4().hex,
                "alertname": "GeoCollectionRunStalled",
                "severity": "warning",
            },
            target_chat_id="oc_test",
            repeat_window_seconds=14_400,
            card_update_seconds=900,
        )
        session.commit()
    assert result.notification_id == ""
    assert result.delivery_enqueued is False


def test_relay_notification_failure_does_not_poison_outer_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url="http://127.0.0.1:18000",
        app_secret_file="",
        verification_token_file="",
        encrypt_key_file="",
        allowed_open_ids_file="",
        link_signing_key_file="",
    )
    monkeypatch.setenv("GEO_ALERT_NOTIFY_CHANNEL", "feishu_app")
    monkeypatch.setattr(
        relay_probe.FeishuBotConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )

    class BrokenNotificationService:
        def __init__(self, session: Any) -> None:
            self.session = session

        def record_alert(self, *_args: object, **_kwargs: object) -> None:
            self.session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(relay_probe, "NotificationService", BrokenNotificationService)
    with SessionLocal() as session:
        assert (
            relay_probe._record_relay_transition(
                session,
                region_gb="310000",
                status="firing",
                note="synthetic",
            )
            is False
        )
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        session.rollback()


class _FakeClient:
    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []

    def send_card(self, **kwargs: Any) -> FeishuResult:
        self.cards.append(kwargs)
        return FeishuResult(data={"message_id": "om_sender"}, request_log_id="safe-log")

    def update_card(self, **_kwargs: Any) -> FeishuResult:
        return FeishuResult(data={}, request_log_id="safe-log")

    def close(self) -> None:
        return


def test_sender_drains_local_outbox_and_persists_message_id(tmp_path: Path) -> None:
    with SessionLocal() as session:
        session.execute(text("DELETE FROM notification.notice"))
        session.commit()
    app_secret = tmp_path / "app-secret"
    link_key = tmp_path / "link-key"
    app_secret.write_text("fake-app-secret", encoding="utf-8")
    link_key.write_text("k" * 32, encoding="utf-8")
    config = FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url="http://127.0.0.1:18000",
        app_secret_file=str(app_secret),
        verification_token_file="",
        encrypt_key_file="",
        allowed_open_ids_file="",
        link_signing_key_file=str(link_key),
    )
    digest, session_id = _registry(tmp_path)
    with SessionLocal() as session:
        pub_id = NotificationService(session).enqueue_assist(
            tenant_pub_id=None,
            session_kind="otp_cli",
            run_pub_id="otp-assist-sender",
            session_id=session_id,
            ticket_sha256=digest,
            platform="yiyan",
            instance_key="yiyan_sh",
            business_key="sender test",
            created_at_epoch=int(datetime.now(UTC).timestamp()),
            expires_at_epoch=int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
            target_chat_id="oc_test",
        )
        session.commit()
    fake = _FakeClient()
    sender = NotificationSender(
        config=config,
        session_factory=SessionLocal,
        client=fake,  # type: ignore[arg-type]
    )
    try:
        assert sender.run_once() >= 1
    finally:
        sender.close()
    with SessionLocal() as session:
        saved = session.scalar(select(Notice).where(Notice.pub_id == pub_id))
        assert saved is not None
        assert saved.message_id == "om_sender"
        assert saved.state == "active"
    assert len(fake.cards) == 1


class _FailingClient(_FakeClient):
    def send_card(self, **_kwargs: Any) -> FeishuResult:
        raise FeishuApiError(
            "feishu_api_http_error",
            code=503,
            retryable=True,
            request_log_id="safe-failure-log",
        )


def test_sender_retries_then_dead_letters_without_sensitive_error(tmp_path: Path) -> None:
    with SessionLocal() as session:
        session.execute(text("DELETE FROM notification.notice"))
        session.commit()
    app_secret = tmp_path / "app-secret"
    link_key = tmp_path / "link-key"
    app_secret.write_text("secret-must-not-appear", encoding="utf-8")
    link_key.write_text("k" * 32, encoding="utf-8")
    config = FeishuBotConfig(
        env="development",
        app_id="cli_test",
        tenant_key="tenant_test",
        chat_id="oc_test",
        public_base_url="https://assist.example",
        api_base_url="http://127.0.0.1:18000",
        app_secret_file=str(app_secret),
        verification_token_file="",
        encrypt_key_file="",
        allowed_open_ids_file="",
        link_signing_key_file=str(link_key),
        sender_max_attempts=2,
    )
    digest, session_id = _registry(tmp_path)
    with SessionLocal() as session:
        pub_id = NotificationService(session).enqueue_assist(
            tenant_pub_id=None,
            session_kind="otp_cli",
            run_pub_id="otp-assist-failure",
            session_id=session_id,
            ticket_sha256=digest,
            platform="yiyan",
            instance_key="yiyan_sh",
            business_key="sender failure test",
            created_at_epoch=int(datetime.now(UTC).timestamp()),
            expires_at_epoch=int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
            target_chat_id="oc_test",
        )
        session.commit()
    sender = NotificationSender(
        config=config,
        session_factory=SessionLocal,
        client=_FailingClient(),  # type: ignore[arg-type]
    )
    try:
        assert sender.run_once() == 1
        with SessionLocal() as session:
            command = session.scalar(select(DeliveryCommand))
            assert command is not None
            assert command.state == "pending" and command.attempts == 1
            command.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert sender.run_once() == 1
    finally:
        sender.close()
    with SessionLocal() as session:
        command = session.scalar(select(DeliveryCommand))
        notice = session.scalar(select(Notice).where(Notice.pub_id == pub_id))
        assert command is not None and command.state == "dead" and command.attempts == 2
        assert command.last_error == "feishu_api_http_error:code_503"
        assert "secret-must-not-appear" not in str(command.last_error)
        assert notice is not None and notice.state == "delivery_failed"
