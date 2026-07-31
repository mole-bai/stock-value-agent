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
    TencentQuoteProvider,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "connectors" / "tencent_quotes.txt"


class TencentQuoteProviderTests(unittest.TestCase):
    def test_parses_all_three_markets(self) -> None:
        payload = FIXTURE.read_bytes()
        seen: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], timeout: float) -> bytes:
            seen.update(url=url, headers=headers, timeout=timeout)
            return payload

        now = datetime(2026, 7, 31, 9, tzinfo=timezone.utc)
        quotes = TencentQuoteProvider(transport=transport).get_many(
            (TENCENT, POP_MART, KWEICHOW_MOUTAI), now=now
        )

        self.assertEqual(
            [quote.price for quote in quotes],
            [Decimal("475.200"), Decimal("162.600"), Decimal("1350.60")],
        )
        self.assertEqual([quote.currency for quote in quotes], ["HKD", "HKD", "CNY"])
        self.assertEqual(quotes[0].previous_close, Decimal("471.800"))
        self.assertEqual(quotes[1].high, Decimal("163.900"))
        self.assertEqual(quotes[2].bid, Decimal("1350.55"))
        self.assertEqual(quotes[2].ask, Decimal("1350.70"))
        self.assertEqual(quotes[2].volume, 5_512_800)
        self.assertEqual(quotes[2].turnover, Decimal("7373462605"))
        self.assertTrue(all(quote.provisional for quote in quotes))
        self.assertTrue(all(quote.freshness is Freshness.FRESH for quote in quotes))
        self.assertEqual(
            seen["url"],
            "https://qt.gtimg.cn/q=r_hk00700,r_hk09992,sh600519",
        )
        self.assertEqual(seen["headers"]["Referer"], "https://gu.qq.com/")

    def test_missing_requested_symbol_fails_closed(self) -> None:
        payload = FIXTURE.read_bytes().splitlines()[0] + b"\n"
        provider = TencentQuoteProvider(transport=lambda *_: payload)

        with self.assertRaisesRegex(Exception, "missing r_hk09992"):
            provider.get_many((TENCENT, POP_MART))


if __name__ == "__main__":
    unittest.main()
