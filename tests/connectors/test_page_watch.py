from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from stock_agent.connectors import (
    ConnectorTransportError,
    Freshness,
    HttpPageResponse,
    OfficialPageWatchProvider,
    TENCENT,
    fetch_official_page,
)


class OfficialPageWatchTests(unittest.TestCase):
    def test_hashes_body_and_preserves_cache_headers(self) -> None:
        body = b"<html><body>official announcements</body></html>"
        seen: dict[str, object] = {}

        def transport(url: str, headers: dict[str, str], timeout: float) -> HttpPageResponse:
            seen.update(url=url, headers=headers, timeout=timeout)
            return HttpPageResponse(
                body=body,
                headers={
                    "eTaG": '"page-v2"',
                    "LAST-MODIFIED": "Fri, 31 Jul 2026 06:00:00 GMT",
                },
                final_url="https://official.example/announcements",
            )

        now = datetime(2026, 7, 31, 8, tzinfo=timezone.utc)
        snapshot = fetch_official_page(
            "https://official.example/start",
            security=TENCENT,
            transport=transport,
            now=now,
        )

        self.assertEqual(snapshot.content_hash, hashlib.sha256(body).hexdigest())
        self.assertEqual(snapshot.etag, '"page-v2"')
        self.assertEqual(
            snapshot.last_modified, "Fri, 31 Jul 2026 06:00:00 GMT"
        )
        self.assertEqual(snapshot.source_url, "https://official.example/announcements")
        self.assertEqual(snapshot.observed_at, now)
        self.assertEqual(snapshot.freshness, Freshness.FRESH)
        self.assertTrue(snapshot.provisional)
        self.assertEqual(snapshot.security, TENCENT)
        self.assertIn("User-Agent", seen["headers"])

    def test_provider_uses_security_mapping(self) -> None:
        body = b"same page"
        provider = OfficialPageWatchProvider(
            {TENCENT.ticker: "https://official.example/tencent"},
            transport=lambda *_: HttpPageResponse(body=body, headers={}),
        )
        snapshot = provider.get_snapshot(
            TENCENT, now=datetime(2026, 7, 31, 8, tzinfo=timezone.utc)
        )
        self.assertEqual(snapshot.security, TENCENT)
        self.assertEqual(snapshot.source_url, "https://official.example/tencent")

    def test_non_success_status_is_transport_failure(self) -> None:
        with self.assertRaisesRegex(ConnectorTransportError, "503"):
            fetch_official_page(
                "https://official.example",
                transport=lambda *_: HttpPageResponse(
                    body=b"temporarily unavailable", headers={}, status=503
                ),
            )


if __name__ == "__main__":
    unittest.main()
