import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from stock_agent.config import load_fundamentals, load_settings
from stock_agent.connectors import Freshness, OfficialPageSnapshot
from stock_agent.events import (
    EventScanStatus,
    OfficialEvent,
    SemanticEventSnapshot,
    compute_semantic_hash,
)
from stock_agent.orchestration import StockMonitoringPipeline, load_static_quote_provider
from stock_agent.state import JsonStateStore


ROOT = Path(__file__).resolve().parents[3]
RUN_TIME = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


class ChangingOfficialPageProvider:
    def __init__(self):
        self.round = 0

    def get_snapshot(self, security, *, now=None):
        self.round += 1
        cycle = (self.round - 1) // 3
        return OfficialPageSnapshot(
            security=security,
            source_url=f"https://example.test/{security.ticker}",
            content_hash=("a" if cycle == 0 else "b") * 64,
            observed_at=now,
            fetched_at=now,
            freshness=Freshness.FRESH,
            provisional=True,
            provider="test_official_page",
        )


class ChangingSemanticEventProvider:
    def __init__(self):
        self.calls = {}

    def scan_security(self, security, *, now=None):
        count = self.calls.get(security.ticker, 0) + 1
        self.calls[security.ticker] = count
        source_id = f"test:{security.ticker}"
        events = [
            OfficialEvent(
                ticker=security.ticker,
                source_id=source_id,
                document_id="baseline",
                title="历史业绩公告",
                document_url=f"https://example.test/{security.ticker}/baseline.pdf",
                published_date=date(2026, 1, 1),
            )
        ]
        if count >= 2:
            events.append(
                OfficialEvent(
                    ticker=security.ticker,
                    source_id=source_id,
                    document_id="new-document",
                    title="新业绩公告",
                    document_url=f"https://example.test/{security.ticker}/new.pdf",
                    published_date=date(2026, 7, 31),
                )
            )
        return (
            SemanticEventSnapshot(
                ticker=security.ticker,
                source_id=source_id,
                source_label="测试官方公告",
                source_url=f"https://example.test/{security.ticker}",
                status=EventScanStatus.EXTRACTED,
                events=tuple(events),
                semantic_hash=compute_semantic_hash(events),
                observed_at=now,
                fetched_at=now,
                message="测试语义快照",
            ),
        )


class FailingQuoteProvider:
    provider_name = "fixture_failing_quote"

    def get_latest(self, _security, *, now=None):
        raise OSError("temporary fixture failure")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(ROOT / "config/watchlist.json")
        self.fundamentals = load_fundamentals(ROOT / "data/fundamentals.json")
        self.quotes = load_static_quote_provider(ROOT / "data/sample_quotes.json")

    def _pipeline(
        self,
        directory,
        page_provider=None,
        semantic_provider=None,
        quote_providers=None,
    ):
        return StockMonitoringPipeline(
            settings=self.settings,
            fundamentals=self.fundamentals,
            quote_providers=quote_providers or [self.quotes],
            state_store=JsonStateStore(Path(directory) / "state.json"),
            output_dir=Path(directory) / "reports",
            official_page_provider=page_provider,
            semantic_event_provider=semantic_provider,
        )

    def test_offline_full_run_emits_three_auditable_wait_views(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._pipeline(directory).run(now=RUN_TIME)
            self.assertEqual(outcome.result["status"], "success")
            self.assertEqual(len(outcome.result["stocks"]), 3)
            self.assertEqual(
                {stock["recommendation"]["action"] for stock in outcome.result["stocks"]},
                {"等待"},
            )
            self.assertTrue(outcome.markdown_receipt.path.exists())
            self.assertTrue(outcome.json_receipt.path.exists())
            self.assertTrue(
                all(
                    stock["recommendation"]["assessment"]["version"]
                    == "value_scorecard.v2"
                    for stock in outcome.result["stocks"]
                )
            )
            markdown = outcome.markdown_receipt.path.read_text(encoding="utf-8")
            self.assertIn("腾讯控股", markdown)
            self.assertIn("建仓价上限", markdown)
            self.assertIn("不构成个性化投资建议", markdown)

    def test_official_page_change_freezes_positive_view_until_review(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = ChangingOfficialPageProvider()
            pipeline = self._pipeline(directory, provider)
            first = pipeline.run(now=RUN_TIME)
            second = pipeline.run(now=RUN_TIME)
            self.assertTrue(all(stock["recommendation"]["action"] == "等待" for stock in first.result["stocks"]))
            self.assertTrue(all(stock["recommendation"]["action"] == "无建议" for stock in second.result["stocks"]))
            self.assertTrue(
                all(stock["events"] for stock in second.result["stocks"])
            )

    def test_old_replay_quote_is_reclassified_stale_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            future = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
            outcome = self._pipeline(directory).run(now=future)
            self.assertEqual(outcome.result["status"], "degraded")
            self.assertTrue(
                all(stock["price"]["freshness"] == "stale" for stock in outcome.result["stocks"])
            )
            self.assertTrue(
                all(stock["recommendation"]["action"] == "无建议" for stock in outcome.result["stocks"])
            )

    def test_successful_quote_fallback_is_audit_detail_not_top_level_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._pipeline(
                directory,
                quote_providers=[FailingQuoteProvider(), self.quotes],
            ).run(now=RUN_TIME)

            self.assertEqual(outcome.result["status"], "success")
            self.assertEqual(outcome.result["warnings"], [])
            for stock in outcome.result["stocks"]:
                quote_audit = stock["audit"]["quote"]
                self.assertEqual(
                    quote_audit["selected_provider"],
                    "sina_personal_prototype_replay",
                )
                self.assertIn("fixture_failing_quote", quote_audit["fallback_failures"][0])

    def test_semantic_baseline_is_quiet_then_new_document_enters_review_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = ChangingSemanticEventProvider()
            pipeline = self._pipeline(directory, semantic_provider=provider)
            first = pipeline.run(now=RUN_TIME)
            second = pipeline.run(now=RUN_TIME)
            self.assertEqual(first.result["pending_reviews"], [])
            self.assertTrue(
                all(stock["recommendation"]["action"] == "等待" for stock in first.result["stocks"])
            )
            self.assertEqual(len(second.result["pending_reviews"]), 3)
            self.assertTrue(
                all(stock["recommendation"]["action"] == "无建议" for stock in second.result["stocks"])
            )
            self.assertEqual(second.result["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
