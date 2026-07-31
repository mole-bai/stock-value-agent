"""Deterministic quiet-hours and repeat-suppression policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, MutableMapping
from zoneinfo import ZoneInfo

from .models import Notification, NotificationDecision, NotificationPriority


@dataclass(frozen=True, slots=True)
class QuietHours:
    timezone: str = "Asia/Shanghai"
    start: time = time(22, 0)
    end: time = time(8, 0)
    bypass_through: NotificationPriority = NotificationPriority.P0

    def contains(self, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current = now.astimezone(ZoneInfo(self.timezone)).time().replace(tzinfo=None)
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= current < self.end
        return current >= self.start or current < self.end

    def defers(self, notification: Notification, *, now: datetime) -> bool:
        return notification.priority > self.bypass_through and self.contains(now)


class NotificationLedger:
    """Use a caller-owned state mapping so dedupe is saved with agent state."""

    def __init__(self, state: MutableMapping[str, Any]) -> None:
        self.state = state
        self.records: MutableMapping[str, Any] = state.setdefault("notifications", {})

    def decide(
        self,
        notification: Notification,
        *,
        now: datetime,
        quiet_hours: QuietHours | None = None,
    ) -> NotificationDecision:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        previous = self.records.get(notification.dedupe_key)
        if isinstance(previous, dict):
            previous_priority = int(previous.get("priority", 99))
            if previous.get("fingerprint") == notification.fingerprint:
                return NotificationDecision(False, "duplicate")
            if int(notification.priority) >= previous_priority and previous.get("content_key") == _content_key(notification):
                return NotificationDecision(False, "same_content_without_priority_upgrade")
        if quiet_hours and quiet_hours.defers(notification, now=now):
            return NotificationDecision(False, "quiet_hours", deferred=True)
        return NotificationDecision(
            True,
            "priority_upgrade"
            if isinstance(previous, dict)
            and int(notification.priority) < int(previous.get("priority", 99))
            else "new_or_changed",
        )

    def mark_sent(self, notification: Notification, *, sent_at: datetime) -> None:
        if sent_at.tzinfo is None or sent_at.utcoffset() is None:
            raise ValueError("sent_at must be timezone-aware")
        self.records[notification.dedupe_key] = {
            "notification_id": notification.notification_id,
            "fingerprint": notification.fingerprint,
            "content_key": _content_key(notification),
            "priority": int(notification.priority),
            "sent_at": sent_at.isoformat(),
        }


def _content_key(notification: Notification) -> str:
    # Priority is deliberately excluded: raising severity for unchanged content
    # is a meaningful resend, lowering it is not.
    return "\u241f".join(
        (
            notification.category,
            notification.title,
            notification.body,
            notification.symbol or "",
            notification.source_url or "",
        )
    )
