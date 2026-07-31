"""Strict, dependency-free models for investor events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CalendarError(ValueError):
    """Raised when an event calendar cannot be trusted."""


class EventConfidence(str, Enum):
    OFFICIAL_CONFIRMED = "official_confirmed"
    VENDOR_EXPECTED = "vendor_expected"
    INFERRED = "inferred"


class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    TENTATIVE = "tentative"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


def _required_text(value: Mapping[str, Any], key: str) -> str:
    text = str(value.get(key, "")).strip()
    if not text:
        raise CalendarError(f"event requires {key}")
    return text


def _http_url(raw: Any) -> str:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise CalendarError("event source_url must be an http(s) URL")
    return value


def _aware_datetime(raw: Any, *, field_name: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CalendarError(f"{field_name} must be ISO-8601") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError(f"{field_name} must include a UTC offset")
    return value


@dataclass(frozen=True, slots=True)
class InvestorEvent:
    event_id: str
    symbol: str
    title: str
    event_type: str
    start: datetime
    end: datetime
    timezone: str
    confidence: EventConfidence
    status: EventStatus
    source_url: str
    reminder_days: tuple[int, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InvestorEvent":
        timezone_name = _required_text(value, "timezone")
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise CalendarError(f"unknown event timezone: {timezone_name}") from exc

        start = _aware_datetime(value.get("start"), field_name="start")
        end_raw = value.get("end")
        end = (
            _aware_datetime(end_raw, field_name="end")
            if end_raw not in (None, "")
            else start + timedelta(hours=1)
        )
        if end <= start:
            raise CalendarError("event end must be later than start")

        # Normalize to the declared civil timezone.  The original instant remains
        # intact, while reports and ICS files use the issuer's local wall clock.
        start = start.astimezone(zone)
        end = end.astimezone(zone)
        try:
            confidence = EventConfidence(_required_text(value, "confidence"))
            status = EventStatus(_required_text(value, "status"))
        except ValueError as exc:
            raise CalendarError(str(exc)) from exc

        raw_days = value.get("reminder_days", [])
        if not isinstance(raw_days, list):
            raise CalendarError("reminder_days must be a list")
        days: list[int] = []
        for raw_day in raw_days:
            if isinstance(raw_day, bool):
                raise CalendarError("reminder days must be integers")
            try:
                day = int(raw_day)
            except (TypeError, ValueError) as exc:
                raise CalendarError("reminder days must be integers") from exc
            if day < 0 or day > 365:
                raise CalendarError("reminder days must be between 0 and 365")
            days.append(day)

        return cls(
            event_id=_required_text(value, "event_id"),
            symbol=_required_text(value, "symbol"),
            title=_required_text(value, "title"),
            event_type=_required_text(value, "event_type"),
            start=start,
            end=end,
            timezone=timezone_name,
            confidence=confidence,
            status=status,
            source_url=_http_url(value.get("source_url")),
            reminder_days=tuple(sorted(set(days), reverse=True)),
        )

    @property
    def is_tentative(self) -> bool:
        return (
            self.status in {EventStatus.TENTATIVE, EventStatus.POSTPONED}
            or self.confidence is not EventConfidence.OFFICIAL_CONFIRMED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "title": self.title,
            "event_type": self.event_type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timezone": self.timezone,
            "confidence": self.confidence.value,
            "status": self.status.value,
            "source_url": self.source_url,
            "reminder_days": list(self.reminder_days),
            "tentative": self.is_tentative,
        }


@dataclass(frozen=True, slots=True)
class EventReminder:
    reminder_id: str
    event: InvestorEvent
    days_before: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "reminder_id": self.reminder_id,
            "days_before": self.days_before,
            "event": self.event.to_dict(),
        }
