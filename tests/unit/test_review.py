import unittest
from datetime import datetime, timezone

from stock_agent.review import ReviewDecision, ReviewQueue


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


class ReviewQueueTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_pending_blocks_positive_view(self):
        state = {}
        queue = ReviewQueue(state)
        first = queue.enqueue(
            symbol="0700.HK",
            document_id="doc-1",
            title="公告",
            source_url="https://example.test/doc-1",
            discovered_at=NOW,
        )
        second = queue.enqueue(
            symbol="0700.HK",
            document_id="doc-1",
            title="另一标题不覆盖原始记录",
            source_url="https://example.test/doc-1",
            discovered_at=NOW,
        )
        self.assertEqual(first, second)
        self.assertTrue(queue.blocks_positive_view("0700.HK"))

    def test_resolution_preserves_audit_record_and_unblocks(self):
        queue = ReviewQueue({})
        item = queue.enqueue(
            symbol="9992.HK",
            document_id="doc-2",
            title="月报表",
            source_url="https://example.test/doc-2",
            discovered_at=NOW,
        )
        resolved = queue.resolve(
            item.review_id,
            decision=ReviewDecision.NON_MATERIAL,
            reviewed_at=NOW,
            note="不改变估值输入",
        )
        self.assertEqual(resolved.status, "reviewed")
        self.assertEqual(resolved.decision, "non_material")
        self.assertFalse(queue.blocks_positive_view("9992.HK"))

    def test_material_item_stays_blocking_until_updated_and_revalued(self):
        queue = ReviewQueue({})
        item = queue.enqueue(
            symbol="600519.SS",
            document_id="doc-3",
            title="重大财务更新",
            source_url="https://example.test/doc-3",
            discovered_at=NOW,
        )
        material = queue.resolve(
            item.review_id,
            decision=ReviewDecision.MATERIAL,
            reviewed_at=NOW,
        )
        self.assertEqual(material.status, "action_required")
        self.assertTrue(queue.blocks_positive_view("600519.SS"))
        complete = queue.resolve(
            item.review_id,
            decision=ReviewDecision.UPDATED_AND_REVALUED,
            reviewed_at=NOW,
            note="财务事实和估值已更新",
        )
        self.assertEqual(complete.status, "reviewed")
        self.assertEqual(
            [record["decision"] for record in complete.history],
            ["material", "updated_and_revalued"],
        )
        self.assertFalse(queue.blocks_positive_view("600519.SS"))


if __name__ == "__main__":
    unittest.main()
