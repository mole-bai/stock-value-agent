from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from stock_agent.connectors import POP_MART, TENCENT
from stock_agent.events import (
    EventScanStatus,
    OFFICIAL_EVENT_SOURCES,
    OfficialEventSemanticProvider,
    OfficialEventSource,
    SourceKind,
)


NOW = datetime(2026, 7, 31, 8, tzinfo=timezone.utc)
IR_SOURCE = OfficialEventSource(
    ticker="0700.HK",
    source_id="fixture_ir",
    label="Fixture IR",
    url="https://official.example/results",
    kind=SourceKind.IR_INDEX,
)
PORTAL_SOURCE = OfficialEventSource(
    ticker="0700.HK",
    source_id="fixture_portal",
    label="Fixture Exchange Portal",
    url="https://exchange.example/search",
    kind=SourceKind.EXCHANGE_PORTAL,
)


class OfficialEventProviderTests(unittest.TestCase):
    def test_extracts_from_injected_transport_and_preserves_final_url(self) -> None:
        seen: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], timeout: float) -> object:
            seen.update(url=url, headers=headers, timeout=timeout)
            return SimpleNamespace(
                body=(
                    b'<div>2026-07-31 <a href="/docs/results.pdf?token=x">'
                    b"Annual Results Announcement</a></div>"
                ),
                headers={"Content-Type": "text/html; charset=utf-8"},
                status=200,
                final_url="https://official.example/investors/results",
            )

        provider = OfficialEventSemanticProvider(
            {TENCENT.ticker: (IR_SOURCE,)}, transport=transport
        )
        snapshot = provider.scan_security(TENCENT, now=NOW)[0]

        self.assertEqual(snapshot.status, EventScanStatus.EXTRACTED)
        self.assertEqual(len(snapshot.events), 1)
        self.assertIsNotNone(snapshot.semantic_hash)
        self.assertFalse(snapshot.coverage_complete)
        self.assertEqual(
            snapshot.events[0].document_url,
            "https://official.example/docs/results.pdf",
        )
        self.assertIn("User-Agent", seen["headers"])

    def test_empty_exchange_html_is_portal_only_not_no_announcements(self) -> None:
        provider = OfficialEventSemanticProvider(
            {TENCENT.ticker: (PORTAL_SOURCE,)},
            transport=lambda *_: b"<html><body>Search requires JavaScript</body></html>",
        )

        snapshot = provider.scan_security(TENCENT, now=NOW)[0]

        self.assertEqual(snapshot.status, EventScanStatus.PORTAL_ONLY)
        self.assertEqual(snapshot.events, ())
        self.assertIsNone(snapshot.semantic_hash)
        self.assertIn("不表示没有公告", snapshot.message)

    def test_empty_ir_html_is_degraded(self) -> None:
        provider = OfficialEventSemanticProvider(
            {TENCENT.ticker: (IR_SOURCE,)},
            transport=lambda *_: b"<html><body>Investor Relations</body></html>",
        )

        snapshot = provider.scan_security(TENCENT, now=NOW)[0]

        self.assertEqual(snapshot.status, EventScanStatus.DEGRADED)
        self.assertIsNone(snapshot.semantic_hash)

    def test_transport_failure_degrades_instead_of_returning_empty_success(self) -> None:
        def fail(*_args: object) -> object:
            raise OSError("fixture network error")

        provider = OfficialEventSemanticProvider(
            {TENCENT.ticker: (IR_SOURCE,)}, transport=fail
        )
        snapshot = provider.scan_security(TENCENT, now=NOW)[0]

        self.assertEqual(snapshot.status, EventScanStatus.DEGRADED)
        self.assertIsNone(snapshot.semantic_hash)
        self.assertIn("无法判断", snapshot.message)

    def test_non_success_http_status_degrades(self) -> None:
        provider = OfficialEventSemanticProvider(
            {TENCENT.ticker: (IR_SOURCE,)},
            transport=lambda *_: SimpleNamespace(
                body=b"unavailable", headers={}, status=503, final_url=IR_SOURCE.url
            ),
        )

        snapshot = provider.scan_security(TENCENT, now=NOW)[0]
        self.assertEqual(snapshot.status, EventScanStatus.DEGRADED)
        self.assertIn("HTTP 503", snapshot.message)

    def test_catalog_covers_each_requested_ticker_with_ir_and_exchange_sources(self) -> None:
        self.assertEqual(set(OFFICIAL_EVENT_SOURCES), {"0700.HK", "9992.HK", "600519.SS"})
        for sources in OFFICIAL_EVENT_SOURCES.values():
            self.assertEqual(
                {source.kind for source in sources},
                {SourceKind.IR_INDEX, SourceKind.EXCHANGE_PORTAL},
            )

    def test_unsupported_security_is_explicit(self) -> None:
        provider = OfficialEventSemanticProvider(transport=lambda *_: b"")
        with self.assertRaisesRegex(ValueError, "no official event sources"):
            provider.scan_security(
                SimpleNamespace(ticker="UNKNOWN"),
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
