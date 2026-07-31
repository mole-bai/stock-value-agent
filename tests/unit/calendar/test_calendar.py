import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from stock_agent.calendar import (
    CalendarError,
    EventConfidence,
    EventStatus,
    InvestorEvent,
    load_event_calendar,
    render_ics,
)


ROOT = Path(__file__).resolve().parents[3]


class EventCalendarTests(unittest.TestCase):
    def test_loads_repo_calendar_and_separates_confirmed_from_inferred(self):
        calendar = load_event_calendar(ROOT / "data/events.json")
        self.assertEqual(len(calendar.events), 3)
        self.assertEqual(calendar.events[0].confidence, EventConfidence.OFFICIAL_CONFIRMED)
        self.assertFalse(calendar.events[0].is_tentative)
        self.assertTrue(calendar.events[1].is_tentative)

    def test_due_reminders_use_each_events_local_calendar_date(self):
        calendar = load_event_calendar(ROOT / "data/events.json")
        reminders = calendar.due_reminders(
            now=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
        )
        self.assertEqual([item.reminder_id for item in reminders], ["0700.HK:results:2026Q2:reminder:7"])

    def test_rejects_naive_times_and_bad_urls(self):
        record = {
            "event_id": "x",
            "symbol": "X",
            "title": "test",
            "event_type": "results",
            "start": "2026-08-01T10:00:00",
            "timezone": "Asia/Shanghai",
            "confidence": "official_confirmed",
            "status": "scheduled",
            "source_url": "file:///tmp/x",
        }
        with self.assertRaises(CalendarError):
            InvestorEvent.from_mapping(record)

    def test_ics_uses_crlf_tzid_and_marks_inferred_events_tentative(self):
        calendar = load_event_calendar(ROOT / "data/events.json")
        value = render_ics(
            calendar.events,
            generated_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        self.assertTrue(value.endswith("\r\n"))
        self.assertIn("DTSTART;TZID=Asia/Hong_Kong:20260812T200000", value)
        self.assertIn("STATUS:TENTATIVE", value)
        self.assertIn("[待确认]", value)


if __name__ == "__main__":
    unittest.main()
