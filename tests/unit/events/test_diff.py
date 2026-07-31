from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from stock_agent.events import (
    EventDiffStatus,
    EventScanStatus,
    OfficialEvent,
    SemanticEventSnapshot,
    compute_semantic_hash,
    diff_event_snapshots,
)


NOW = datetime(2026, 7, 31, 8, tzinfo=timezone.utc)


def event(identifier: str, title: str) -> OfficialEvent:
    return OfficialEvent(
        ticker="0700.HK",
        source_id="fixture_ir",
        document_id=identifier,
        title=title,
        document_url=f"https://official.example/{identifier}.pdf",
        published_date=date(2026, 7, 31),
    )


def extracted(*events: OfficialEvent) -> SemanticEventSnapshot:
    return SemanticEventSnapshot(
        ticker="0700.HK",
        source_id="fixture_ir",
        source_label="Fixture IR",
        source_url="https://official.example/results",
        status=EventScanStatus.EXTRACTED,
        events=tuple(events),
        semantic_hash=compute_semantic_hash(events),
        observed_at=NOW,
        fetched_at=NOW,
        message="fixture extraction",
    )


def degraded() -> SemanticEventSnapshot:
    return SemanticEventSnapshot(
        ticker="0700.HK",
        source_id="fixture_ir",
        source_label="Fixture IR",
        source_url="https://official.example/results",
        status=EventScanStatus.DEGRADED,
        events=(),
        semantic_hash=None,
        observed_at=NOW,
        fetched_at=NOW,
        message="fixture degraded",
    )


class EventDiffTests(unittest.TestCase):
    def test_first_scan_is_baseline_not_a_flood_of_new_alerts(self) -> None:
        current = extracted(event("a", "Annual Results"))

        result = diff_event_snapshots(None, current)

        self.assertEqual(result.status, EventDiffStatus.BASELINE)
        self.assertTrue(result.is_baseline)
        self.assertEqual(result.new_events, ())

    def test_same_semantics_are_unchanged(self) -> None:
        current = extracted(event("a", "Annual Results"))
        result = diff_event_snapshots(current, current)

        self.assertEqual(result.status, EventDiffStatus.UNCHANGED)
        self.assertFalse(result.has_detected_change)
        self.assertIn("不等同于", result.message)

    def test_new_document_is_reported(self) -> None:
        old = extracted(event("a", "Annual Results"))
        added = event("b", "Dividend Announcement")
        current = extracted(event("a", "Annual Results"), added)

        result = diff_event_snapshots(old, current)

        self.assertEqual(result.status, EventDiffStatus.CHANGED)
        self.assertEqual(result.new_events, (added,))
        self.assertEqual(result.updated_events, ())

    def test_same_document_with_changed_title_is_an_update(self) -> None:
        old = extracted(event("a", "Annual Results"))
        revised = event("a", "Annual Results — Revised")
        current = extracted(revised)

        result = diff_event_snapshots(old, current)

        self.assertEqual(result.status, EventDiffStatus.CHANGED)
        self.assertEqual(result.updated_events, (revised,))

    def test_degraded_current_scan_is_unknown_not_unchanged(self) -> None:
        old = extracted(event("a", "Annual Results"))

        result = diff_event_snapshots(old, degraded())

        self.assertEqual(result.status, EventDiffStatus.UNKNOWN)
        self.assertEqual(result.new_events, ())
        self.assertIn("不表示没有新公告", result.message)

    def test_extracted_snapshot_cannot_be_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one event"):
            SemanticEventSnapshot(
                ticker="0700.HK",
                source_id="fixture_ir",
                source_label="Fixture IR",
                source_url="https://official.example/results",
                status=EventScanStatus.EXTRACTED,
                events=(),
                semantic_hash="0" * 64,
                observed_at=NOW,
                fetched_at=NOW,
                message="invalid fixture",
            )

    def test_snapshot_round_trip_is_lossless(self) -> None:
        original = extracted(event("a", "Annual Results"))
        restored = SemanticEventSnapshot.from_dict(original.to_dict())
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
