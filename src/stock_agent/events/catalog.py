"""Official event-index URLs for the initial three-stock watchlist."""

from __future__ import annotations

from .models import OfficialEventSource, SourceKind


TENCENT_IR_RESULTS = OfficialEventSource(
    ticker="0700.HK",
    source_id="tencent_ir_results",
    label="腾讯投资者关系—季度及年度业绩",
    url="https://www.tencent.com/en-us/investors/quarter-result.html",
    kind=SourceKind.IR_INDEX,
    language="en",
)

TENCENT_HKEX_ANNOUNCEMENTS = OfficialEventSource(
    ticker="0700.HK",
    source_id="hkex_tencent_announcements",
    label="港交所披露易—腾讯公告检索",
    url=(
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?"
        "category=0&lang=EN&market=SEHK&stockId=7609"
    ),
    kind=SourceKind.EXCHANGE_PORTAL,
    language="en",
)

POP_MART_IR = OfficialEventSource(
    ticker="9992.HK",
    source_id="popmart_ir",
    label="泡泡玛特投资者关系",
    url="https://www.popmart.com.cn/home/investor",
    kind=SourceKind.IR_INDEX,
    language="zh",
)

POP_MART_HKEX_ANNOUNCEMENTS = OfficialEventSource(
    ticker="9992.HK",
    source_id="hkex_popmart_announcements",
    label="港交所披露易—泡泡玛特公告检索",
    url=(
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?"
        "category=0&lang=EN&market=SEHK&stockId=1000068054"
    ),
    kind=SourceKind.EXCHANGE_PORTAL,
    language="en",
)

MOUTAI_FINANCIAL_REPORTS = OfficialEventSource(
    ticker="600519.SS",
    source_id="moutai_financial_reports",
    label="贵州茅台—财务报告",
    url="https://www.moutaichina.com/mtgf/tzzgx/cwbg/index.html",
    kind=SourceKind.IR_INDEX,
    language="zh",
)

MOUTAI_SSE_ANNOUNCEMENTS = OfficialEventSource(
    ticker="600519.SS",
    # Version the semantic baseline because the structured API exposes a much
    # broader record set than the former HTML portal parser.
    source_id="sse_moutai_announcements_api_v1",
    label="上海证券交易所—贵州茅台公告",
    url=(
        "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do?"
        "isPagination=true&productId=600519&keyWord=&"
        "securityType=0101%2C120100%2C020100%2C020200%2C120200&"
        "pageHelp.pageSize=50&pageHelp.pageCount=50&pageHelp.pageNo=1&"
        "pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=5"
    ),
    kind=SourceKind.STRUCTURED_API,
    language="zh",
)


OFFICIAL_EVENT_SOURCES: dict[str, tuple[OfficialEventSource, ...]] = {
    "0700.HK": (TENCENT_IR_RESULTS, TENCENT_HKEX_ANNOUNCEMENTS),
    # Pop Mart's IR landing page is JavaScript-only and duplicates HKEX filings.
    # Monitor the authoritative, machine-comparable HKEX list instead of issuing
    # a permanent parser warning for a redundant page.
    "9992.HK": (POP_MART_HKEX_ANNOUNCEMENTS,),
    "600519.SS": (MOUTAI_FINANCIAL_REPORTS, MOUTAI_SSE_ANNOUNCEMENTS),
}


def sources_for(ticker: str) -> tuple[OfficialEventSource, ...]:
    """Return configured sources without exposing the mutable catalog mapping."""

    return tuple(OFFICIAL_EVENT_SOURCES.get(ticker, ()))
