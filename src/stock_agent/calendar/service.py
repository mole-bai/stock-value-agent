"""Loading, querying and reminder calculation for investor events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import CalendarError, EventReminder, EventStatus, InvestorEvent


@dataclass(frozen=True, slots=True)
class EventCalendar:
    events: tuple[InvestorEvent, ...]
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        identifiers = [event.event_id for event in self.events]
        if len(identifiers) != len(set(identifiers)):
            raise CalendarError("event_id values must be unique")

    def upcoming(
        self,
        *,
        now: datetime,
        days: int = 30,
        symbols: Iterable[str] | None = None,
    ) -> tuple[InvestorEvent, ...]:
        _require_aware(now)
        if days < 0:
            raise CalendarError("days must not be negative")
        selected = set(symbols) if symbols is not None else None
        horizon = now + timedelta(days=days)
        return tuple(
            sorted(
                (
                    event
                    for event in self.events
                    if event.status not in {EventStatus.CANCELLED, EventStatus.COMPLETED}
                    and event.end >= now
                    and event.start <= horizon
                    and (selected is None or event.symbol in selected)
                ),
                key=lambda event: (event.start, event.symbol, event.event_id),
            )
        )

    def due_reminders(self, *, now: datetime) -> tuple[EventReminder, ...]:
        """Return reminders due on the event's local calendar date.

        Daily scheduling is intentionally date-based: running at 08:00 or 18:30
        on the same local day yields the same reminder fingerprint.
        """

        _require_aware(now)
        reminders: list[EventReminder] = []
        for event in self.events:
            if event.status in {EventStatus.CANCELLED, EventStatus.COMPLETED}:
                continue
            local_now = now.astimezone(event.start.tzinfo)
            days_before = (event.start.date() - local_now.date()).days
            if days_before in event.reminder_days and local_now <= event.end:
                reminders.append(
                    EventReminder(
                        reminder_id=f"{event.event_id}:reminder:{days_before}",
                        event=event,
                        days_before=days_before,
                    )
                )
        return tuple(
            sorted(reminders, key=lambda reminder: (reminder.event.start, reminder.reminder_id))
        )


def load_event_calendar(path: str | Path) -> EventCalendar:
    try:
        document: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalendarError(f"cannot load event calendar {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise CalendarError("event calendar root must be an object")
    if document.get("schema_version") != 1:
        raise CalendarError("unsupported event calendar schema")
    raw_events = document.get("events")
    if not isinstance(raw_events, list):
        raise CalendarError("event calendar requires an events list")
    observed_raw = document.get("observed_at")
    observed_at = _parse_aware(observed_raw, "observed_at") if observed_raw else None
    return EventCalendar(
        events=tuple(InvestorEvent.from_mapping(item) for item in raw_events),
        observed_at=observed_at,
    )


def _parse_aware(raw: Any, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError(f"{field} must be ISO-8601") from exc
    _require_aware(value)
    return value


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError("now must be timezone-aware")
