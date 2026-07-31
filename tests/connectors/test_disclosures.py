from __future__ import annotations

import unittest
from datetime import datetime, timezone

from stock_agent.connectors import (
    Disclosure,
    Freshness,
    HKEX_POP_MART_URL,
    HKEX_TENCENT_URL,
    KWEICHOW_MOUTAI,
    POP_MART,
    ProviderStatus,
    SSE_MOUTAI_URL,
    StaticDisclosureProvider,
    TENCENT,
    WATCHLIST,
    OfficialDisclosurePortalProvider,
)


UTC = timezone.utc


class OfficialDisclosurePortalTests(unittest.TestCase):
    def test_initial_watchlist_has_requested_symbols(self) -> None:
        self.assertEqual(
            [security.ticker for security in WATCHLIST],
            ["0700.HK", "9992.HK", "600519.SS"],
        )

    def test_watchlist_has_official_exchange_pages(self) -> None:
        provider = OfficialDisclosurePortalProvider()
        now = datetime(2026, 7, 31, 8, tzinfo=UTC)

        self.assertEqual(provider.get_portal(TENCENT, now=now).source_url, HKEX_TENCENT_URL)
        self.assertEqual(
            provider.get_portal(POP_MART, now=now).source_url, HKEX_POP_MART_URL
        )
        self.assertEqual(
            provider.get_portal(KWEICHOW_MOUTAI, now=now).source_url,
            SSE_MOUTAI_URL,
        )

    def test_portal_only_scan_cannot_be_mistaken_for_no_announcements(self) -> None:
        now = datetime(2026, 7, 31, 8, tzinfo=UTC)
        batch = OfficialDisclosurePortalProvider().get_since(TENCENT, now=now)

        self.assertEqual(batch.items, ())
        self.assertEqual(batch.status, ProviderStatus.PORTAL_ONLY)
        self.assertEqual(batch.freshness, Freshness.UNKNOWN)
        self.assertTrue(batch.provisional)
        self.assertIn("does not mean no new disclosures", batch.message or "")
        self.assertEqual(batch.observed_at, now)
        self.assertTrue(batch.source_url.startswith("https://www1.hkexnews.hk/"))


class StaticDisclosureProviderTests(unittest.TestCase):
    def test_filters_newer_records_and_returns_complete_snapshot(self) -> None:
        first = _disclosure("a", datetime(2026, 3, 1, tzinfo=UTC))
        second = _disclosure("b", datetime(2026, 4, 1, tzinfo=UTC))
        provider = StaticDisclosureProvider([first, second])
        batch = provider.get_since(
            POP_MART,
            since=datetime(2026, 3, 15, tzinfo=UTC),
            now=datetime(2026, 4, 2, tzinfo=UTC),
        )

        self.assertEqual([item.disclosure_id for item in batch.items], ["b"])
        self.assertEqual(batch.status, ProviderStatus.COMPLETE)
        self.assertEqual(batch.freshness, Freshness.FRESH)
        self.assertFalse(batch.provisional)


def _disclosure(identifier: str, published_at: datetime) -> Disclosure:
    return Disclosure(
        security=POP_MART,
        disclosure_id=identifier,
        title=f"Announcement {identifier}",
        category="announcement",
        published_at=published_at,
        observed_at=published_at,
        fetched_at=published_at,
        source_url=f"https://www1.hkexnews.hk/{identifier}.pdf",
        freshness=Freshness.FRESH,
        provisional=False,
        provider="fixture",
    )


if __name__ == "__main__":
    unittest.main()
