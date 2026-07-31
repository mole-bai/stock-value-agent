# Connector layer

This package is dependency-free and keeps provider provenance on every result:
`source_url`, `observed_at`, `freshness`, and `provisional` are always explicit.

```python
from stock_agent.connectors import CurlTransport, SinaQuoteProvider, TENCENT

quote = SinaQuoteProvider(transport=CurlTransport()).get_latest(TENCENT)
```

`SinaQuoteProvider` is the initial three-stock prototype source;
`YahooChartQuoteProvider` remains an alternative. Both are deliberately named
and marked as personal prototype sources. They are not official exchange feeds,
and every quote they return is provisional. Replace them with a licensed
provider before production, redistribution, or commercial use. `CurlTransport`
is available on machines whose Python CA store is broken; it does not disable
TLS verification and applies timeout, protocol, redirect, and response-size
limits.

The disclosure package offers two different capabilities:

- `OfficialDisclosurePortalProvider` maps the initial watchlist to HKEXnews or
  SSE official issuer search pages. Its scan status is `portal_only`, so an
  empty list cannot be interpreted as “no new announcements.”
- `OfficialPageWatchProvider` GETs a configured official page and records its
  raw SHA-256, ETag, Last-Modified, final URL, and observation time. A hash
  change is a review trigger, not proof of a new filing.

Both network providers accept injected transports for deterministic tests.
`StaticQuoteProvider` and `StaticDisclosureProvider` support offline demos and
manually curated snapshots.
