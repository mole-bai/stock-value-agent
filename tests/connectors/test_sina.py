from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from stock_agent.connectors import (
    KWEICHOW_MOUTAI,
    POP_MART,
    TENCENT,
    Freshness,
    SinaQuoteProvider,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "connectors" / "sina_quotes.txt"


class SinaQuoteProviderTests(unittest.TestCase):
    def test_parses_all_three_markets_with_required_referer(self) -> None:
        payload = FIXTURE.read_bytes()
        seen: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], timeout: float) -> bytes:
            seen.update(url=url, headers=headers, timeout=timeout)
            return payload

        now = datetime(2026, 7, 31, 9, tzinfo=timezone.utc)
        quotes = SinaQuoteProvider(transport=transport).get_many(
            (TENCENT, POP_MART, KWEICHOW_MOUTAI), now=now
        )

        self.assertEqual([quote.price for quote in quotes], [
            Decimal("475.200"), Decimal("162.600"), Decimal("1350.600")
        ])
        self.assertEqual([quote.currency for quote in quotes], ["HKD", "HKD", "CNY"])
        self.assertEqual(quotes[0].previous_close, Decimal("474.800"))
        self.assertEqual(quotes[1].bid, Decimal("162.500"))
        self.assertEqual(quotes[2].ask, Decimal("1350.600"))
        self.assertEqual(quotes[2].volume, 2_314_567)
        self.assertEqual(quotes[2].turnover, Decimal("3123456789.000"))
        self.assertTrue(all(quote.provisional for quote in quotes))
        self.assertTrue(all(quote.freshness is Freshness.FRESH for quote in quotes))
        self.assertEqual(
            seen["url"],
            "https://hq.sinajs.cn/list=hk00700,hk09992,sh600519",
        )
        self.assertEqual(
            seen["headers"]["Referer"], "https://finance.sina.com.cn/"
        )

    def test_missing_requested_symbol_fails_closed(self) -> None:
        payload = FIXTURE.read_bytes().splitlines()[0] + b"\n"
        provider = SinaQuoteProvider(transport=lambda *_: payload)
        with self.assertRaisesRegex(Exception, "missing hk09992"):
            provider.get_many((TENCENT, POP_MART))

    def test_accepts_hk_market_time_without_seconds(self) -> None:
        payload = FIXTURE.read_bytes().replace(b"16:08:00", b"16:08")
        provider = SinaQuoteProvider(transport=lambda *_: payload)
        quote = provider.get_latest(
            TENCENT, now=datetime(2026, 7, 31, 9, tzinfo=timezone.utc)
        )
        self.assertEqual(
            quote.observed_at,
            datetime(2026, 7, 31, 8, 8, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
