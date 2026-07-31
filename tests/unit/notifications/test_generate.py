import unittest
from datetime import datetime, timezone

from stock_agent.notifications import NotificationPriority, build_run_notifications


NOW = datetime(2026, 7, 31, 9, tzinfo=timezone.utc)


class NotificationGenerationTests(unittest.TestCase):
    def test_baseline_does_not_emit_threshold_or_recommendation_noise(self):
        result = {"delta": {"baseline": True, "stocks": []}, "stocks": []}
        self.assertEqual(build_run_notifications(result, now=NOW), ())

    def test_crossing_entry_threshold_emits_p2_with_stable_key(self):
        result = {
            "delta": {
                "baseline": False,
                "stocks": [
                    {
                        "symbol": "0700.HK",
                        "price": {"kind": "changed", "previous": "430", "current": "410"},
                        "signals": [],
                        "recommendation": {
                            "action": {"kind": "unchanged"},
                            "reason_codes": [],
                        },
                    }
                ],
            },
            "stocks": [
                {
                    "symbol": "0700.HK",
                    "price": {"currency": "HKD"},
                    "audit": {
                        "recommendation": {
                            "price_bands": {"entry_price_ceiling": "416", "expensive_price": "660"}
                        }
                    },
                }
            ],
        }
        notices = build_run_notifications(result, now=NOW)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].priority, NotificationPriority.P2)
        self.assertEqual(notices[0].dedupe_key, "threshold:0700.HK:entry")

    def test_pending_review_and_near_confirmed_event_are_tiered(self):
        result = {
            "delta": {"baseline": True, "stocks": []},
            "stocks": [],
            "pending_reviews": [
                {
                    "review_id": "0700.HK:doc",
                    "symbol": "0700.HK",
                    "title": "业绩公告",
                    "source_url": "https://example.test/doc",
                }
            ],
            "calendar_reminders": [
                {
                    "reminder_id": "evt:1",
                    "days_before": 1,
                    "event": {
                        "event_id": "evt",
                        "symbol": "0700.HK",
                        "title": "业绩会",
                        "start": "2026-08-01T20:00:00+08:00",
                        "confidence": "official_confirmed",
                        "source_url": "https://example.test/event",
                    },
                }
            ],
        }
        notices = build_run_notifications(result, now=NOW)
        self.assertEqual([item.priority for item in notices], [NotificationPriority.P1, NotificationPriority.P2])


if __name__ == "__main__":
    unittest.main()
