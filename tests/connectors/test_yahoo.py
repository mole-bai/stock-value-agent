from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from stock_agent.connectors import (
    ConnectorDataError,
    ConnectorTransportError,
    Freshness,
    TENCENT,
    YahooChartQuoteProvider,
)


FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "connectors" / "yahoo_chart_0700.json"
)


class YahooChartQuoteProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = FIXTURE.read_bytes()

    def test_parses_latest_non_null_daily_candle_with_provenance(self) -> None:
        seen: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], timeout: float) -> bytes:
            seen.update(url=url, headers=headers, timeout=timeout)
            return self.payload

        now = datetime(2024, 9, 4, 8, tzinfo=timezone.utc)
        quote = YahooChartQuoteProvider(http_get=transport).get_latest(TENCENT, now=now)

        self.assertEqual(quote.price, Decimal("375.8"))
        self.assertEqual(quote.previous_close, Decimal("371.4"))
        self.assertEqual(quote.open, Decimal("372.0"))
        self.assertEqual(quote.high, Decimal("378.2"))
        self.assertEqual(quote.low, Decimal("371.2"))
        self.assertEqual(quote.volume, 20_500_000)
        self.assertEqual(quote.currency, "HKD")
        self.assertEqual(
            quote.observed_at, datetime(2024, 9, 3, 7, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(quote.fetched_at, now)
        self.assertEqual(quote.freshness, Freshness.FRESH)
        self.assertTrue(quote.provisional)
        self.assertIn("0700.HK", str(seen["url"]))
        self.assertEqual(quote.source_url, seen["url"])
        self.assertIn("User-Agent", seen["headers"])

    def test_marks_old_observation_stale(self) -> None:
        provider = YahooChartQuoteProvider(http_get=lambda *_: self.payload)
        quote = provider.get_latest(
            TENCENT, now=datetime(2024, 9, 10, 8, tzinfo=timezone.utc)
        )
        self.assertEqual(quote.freshness, Freshness.STALE)

    def test_surfaces_provider_error_as_data_error(self) -> None:
        payload = json.dumps(
            {
                "chart": {
                    "result": None,
                    "error": {"code": "Not Found", "description": "No data found"},
                }
            }
        ).encode()
        provider = YahooChartQuoteProvider(http_get=lambda *_: payload)
        with self.assertRaisesRegex(ConnectorDataError, "No data found"):
            provider.get_latest(TENCENT)

    def test_wraps_injected_transport_failures(self) -> None:
        def broken(*_: object) -> bytes:
            raise OSError("offline")

        provider = YahooChartQuoteProvider(http_get=broken)
        with self.assertRaises(ConnectorTransportError):
            provider.get_latest(TENCENT)

    def test_rejects_naive_now(self) -> None:
        provider = YahooChartQuoteProvider(http_get=lambda *_: self.payload)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            provider.get_latest(TENCENT, now=datetime(2024, 9, 4, 8))


if __name__ == "__main__":
    unittest.main()
