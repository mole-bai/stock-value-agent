"""RFC 5545 compatible-enough iCalendar rendering for local import."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import EventStatus, InvestorEvent


def render_ics(
    events: Iterable[InvestorEvent], *, generated_at: datetime | None = None
) -> str:
    stamp = generated_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    stamp_text = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Stock Value Agent//Investor Events//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in sorted(events, key=lambda item: (item.start, item.event_id)):
        status = {
            EventStatus.CANCELLED: "CANCELLED",
            EventStatus.TENTATIVE: "TENTATIVE",
            EventStatus.POSTPONED: "TENTATIVE",
        }.get(event.status, "CONFIRMED")
        summary = f"[待确认] {event.title}" if event.is_tentative else event.title
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_escape(event.event_id)}@stock-agent.local",
                f"DTSTAMP:{stamp_text}",
                f"DTSTART;TZID={_parameter(event.timezone)}:{_local_time(event.start)}",
                f"DTEND;TZID={_parameter(event.timezone)}:{_local_time(event.end)}",
                f"SUMMARY:{_escape(summary)}",
                f"DESCRIPTION:{_escape(event.symbol + ' | ' + event.confidence.value)}",
                f"STATUS:{status}",
                f"URL:{_escape(event.source_url)}",
                f"X-STOCK-AGENT-CONFIDENCE:{event.confidence.value}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold_line(line)]
    return "\r\n".join(folded) + "\r\n"


def _local_time(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _parameter(value: str) -> str:
    # ZoneInfo identifiers in our validated model are safe tokens in practice.
    return value.replace(";", "").replace(":", "").replace(",", "")


def _fold_line(line: str, limit: int = 75) -> list[str]:
    """Fold by UTF-8 octets without splitting a code point."""

    if len(line.encode("utf-8")) <= limit:
        return [line]
    result: list[str] = []
    current = ""
    current_limit = limit
    for character in line:
        candidate = current + character
        if current and len(candidate.encode("utf-8")) > current_limit:
            result.append(current)
            current = " " + character
            current_limit = limit
        else:
            current = candidate
    if current:
        result.append(current)
    return result
