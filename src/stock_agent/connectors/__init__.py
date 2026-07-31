"""Pluggable data connectors for the stock monitoring agent."""

from .base import (
    ConnectorDataError,
    ConnectorError,
    ConnectorTransportError,
    DisclosurePortalProvider,
    DisclosureProvider,
    OfficialPageProvider,
    QuoteProvider,
    UnsupportedSecurityError,
)
from .catalog import (
    KWEICHOW_MOUTAI,
    POP_MART,
    TENCENT,
    WATCHLIST,
    WATCHLIST_BY_TICKER,
)
from .disclosures import (
    HKEX_POP_MART_URL,
    HKEX_TENCENT_URL,
    OFFICIAL_DISCLOSURE_PORTALS,
    SSE_MOUTAI_URL,
    OfficialDisclosurePortalProvider,
    StaticDisclosureProvider,
)
from .models import (
    Disclosure,
    DisclosurePortal,
    Freshness,
    OfficialPageSnapshot,
    ProviderBatch,
    ProviderStatus,
    Quote,
    Security,
    classify_freshness,
)
from .static import StaticQuoteProvider
from .sina import SINA_SYMBOLS, SinaQuoteProvider
from .tencent import TENCENT_QUOTE_SYMBOLS, TencentQuoteProvider
from .yahoo import YahooChartQuoteProvider
from .page_watch import (
    HttpPageResponse,
    OfficialPageWatchProvider,
    PageTransport,
    fetch_official_page,
)
from .transports import CurlTransport, HttpResponse, Transport

__all__ = [
    "ConnectorDataError",
    "ConnectorError",
    "ConnectorTransportError",
    "CurlTransport",
    "Disclosure",
    "DisclosurePortal",
    "DisclosurePortalProvider",
    "DisclosureProvider",
    "Freshness",
    "HKEX_POP_MART_URL",
    "HKEX_TENCENT_URL",
    "KWEICHOW_MOUTAI",
    "OFFICIAL_DISCLOSURE_PORTALS",
    "OfficialDisclosurePortalProvider",
    "OfficialPageProvider",
    "OfficialPageSnapshot",
    "OfficialPageWatchProvider",
    "POP_MART",
    "ProviderBatch",
    "ProviderStatus",
    "HttpPageResponse",
    "HttpResponse",
    "PageTransport",
    "Quote",
    "QuoteProvider",
    "SSE_MOUTAI_URL",
    "SINA_SYMBOLS",
    "Security",
    "SinaQuoteProvider",
    "StaticDisclosureProvider",
    "StaticQuoteProvider",
    "TENCENT",
    "TENCENT_QUOTE_SYMBOLS",
    "TencentQuoteProvider",
    "Transport",
    "UnsupportedSecurityError",
    "WATCHLIST",
    "WATCHLIST_BY_TICKER",
    "YahooChartQuoteProvider",
    "classify_freshness",
    "fetch_official_page",
]
