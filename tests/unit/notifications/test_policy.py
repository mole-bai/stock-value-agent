import json
import tempfile
import unittest
from datetime import datetime, time, timezone
from pathlib import Path

from stock_agent.notifications import (
    LocalOutbox,
    Notification,
    NotificationLedger,
    NotificationPriority,
    QuietHours,
)


NOW = datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)


def notice(priority=NotificationPriority.P2, body="进入关注区间"):
    return Notification(
        notification_id="n-1",
        dedupe_key="0700.HK:entry_threshold",
        priority=priority,
        category="valuation_threshold",
        title="腾讯价格进入建仓关注区",
        body=body,
        created_at=NOW,
        symbol="0700.HK",
        source_url="https://example.test/source",
    )


class NotificationPolicyTests(unittest.TestCase):
    def test_duplicate_is_suppressed_and_priority_upgrade_resends(self):
        state = {}
        ledger = NotificationLedger(state)
        original = notice()
        self.assertTrue(ledger.decide(original, now=NOW).send)
        ledger.mark_sent(original, sent_at=NOW)
        self.assertEqual(ledger.decide(original, now=NOW).reason, "duplicate")
        upgraded = notice(NotificationPriority.P1)
        decision = ledger.decide(upgraded, now=NOW)
        self.assertTrue(decision.send)
        self.assertEqual(decision.reason, "priority_upgrade")

    def test_quiet_hours_defer_routine_but_not_p0(self):
        quiet = QuietHours(start=time(22), end=time(8))
        routine = NotificationLedger({}).decide(notice(), now=NOW, quiet_hours=quiet)
        urgent = NotificationLedger({}).decide(
            notice(NotificationPriority.P0), now=NOW, quiet_hours=quiet
        )
        self.assertTrue(routine.deferred)
        self.assertFalse(routine.send)
        self.assertTrue(urgent.send)

    def test_local_outbox_writes_atomic_auditable_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = LocalOutbox(directory).deliver(notice())
            payload = json.loads(receipt.json.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["priority"], "P2")
            self.assertEqual(payload["symbol"], "0700.HK")
            self.assertTrue(receipt.markdown.path.exists())


if __name__ == "__main__":
    unittest.main()
