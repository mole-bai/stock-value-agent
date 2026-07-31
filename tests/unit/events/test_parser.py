from __future__ import annotations

import unittest
from datetime import date

from stock_agent.events import (
    OfficialEventSource,
    SourceKind,
    canonicalize_document_url,
    compute_semantic_hash,
    extract_date,
    parse_official_event_html,
)


SOURCE = OfficialEventSource(
    ticker="0700.HK",
    source_id="fixture_ir",
    label="Fixture IR",
    url="https://official.example/investors/results.html",
    kind=SourceKind.IR_INDEX,
)


class SemanticParserTests(unittest.TestCase):
    def test_dynamic_tokens_page_chrome_and_whitespace_do_not_change_hash(self) -> None:
        first = """
        <html><head><meta name="csrf" content="first"></head><body>
          <div class="announcement">
            <span>2026-07-31</span>
            <a href="/docs/20260731-results.pdf?token=abc&amp;timestamp=1">
              Annual     Results
            </a>
          </div>
          <script>window.requestToken = "abc";</script>
        </body></html>
        """
        second = """
        <html><head><meta name="csrf" content="second"></head><body>
          <div class="announcement"> 2026-07-31
            <a href="/docs/20260731-results.pdf?timestamp=999&amp;token=xyz">Annual Results</a>
          </div>
          <script>window.requestToken = "different";</script>
        </body></html>
        """

        events_one = parse_official_event_html(first, source=SOURCE)
        events_two = parse_official_event_html(second, source=SOURCE)

        self.assertEqual(len(events_one), 1)
        self.assertEqual(len(events_two), 1)
        self.assertEqual(events_one, events_two)
        self.assertEqual(
            events_one[0].document_url,
            "https://official.example/docs/20260731-results.pdf",
        )
        self.assertEqual(compute_semantic_hash(events_one), compute_semantic_hash(events_two))

    def test_genuinely_added_announcement_changes_hash(self) -> None:
        baseline = """
        <ul><li>31 July 2026
          <a href="/docs/results-2026.pdf">Annual Results Announcement</a>
        </li></ul>
        """
        changed = baseline + """
        <ul><li>1 August 2026
          <a href="/docs/dividend-2026.pdf">Dividend Announcement</a>
        </li></ul>
        """

        old_events = parse_official_event_html(baseline, source=SOURCE)
        new_events = parse_official_event_html(changed, source=SOURCE)

        self.assertEqual(len(old_events), 1)
        self.assertEqual(len(new_events), 2)
        self.assertNotEqual(
            compute_semantic_hash(old_events), compute_semantic_hash(new_events)
        )

    def test_duplicate_document_links_are_deduplicated(self) -> None:
        html = """
        <div>2026-03-18
          <a href="/docs/fy2025.pdf?token=one">FY2025 Results</a>
          <a href="/docs/fy2025.pdf?token=two">FY2025 Full-Year Results Announcement</a>
        </div>
        """

        events = parse_official_event_html(html, source=SOURCE)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "FY2025 Full-Year Results Announcement")

    def test_extracts_chinese_and_english_dates(self) -> None:
        self.assertEqual(extract_date("发布日期：2026年4月25日"), date(2026, 4, 25))
        self.assertEqual(extract_date("25 April 2026"), date(2026, 4, 25))
        self.assertEqual(extract_date("April 25, 2026"), date(2026, 4, 25))

    def test_navigation_only_html_yields_no_claimable_events(self) -> None:
        html = """
        <nav>
          <a href="/about.html">About us</a>
          <a href="/investors/results.html">Quarterly results</a>
        </nav>
        """

        self.assertEqual(parse_official_event_html(html, source=SOURCE), ())

    def test_url_keeps_stable_document_id_but_drops_tracking(self) -> None:
        canonical = canonicalize_document_url(
            "/download?documentId=A-100&amp;token=secret&amp;utm_source=email",
            base_url=SOURCE.url,
        )

        self.assertEqual(
            canonical,
            "https://official.example/download?documentid=A-100",
        )


if __name__ == "__main__":
    unittest.main()
