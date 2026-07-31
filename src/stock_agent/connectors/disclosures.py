"""Disclosure providers and official portal mappings for the initial watchlist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from .base import DisclosurePortalProvider, DisclosureProvider, UnsupportedSecurityError
from .catalog import KWEICHOW_MOUTAI, POP_MART, TENCENT
from .models import (
    Disclosure,
    DisclosurePortal,
    Freshness,
    ProviderBatch,
    ProviderStatus,
    Security,
    classify_freshness,
    ensure_utc,
)


HKEX_TENCENT_URL = (
    "https://www1.hkexnews.hk/search/titlesearch.xhtml?"
    "category=0&lang=EN&market=SEHK&stockId=7609"
)
HKEX_POP_MART_URL = (
    "https://www1.hkexnews.hk/search/titlesearch.xhtml?"
    "category=0&lang=EN&market=SEHK&stockId=1000068054"
)
SSE_MOUTAI_URL = (
    "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
    "index.shtml?productId=600519"
)

OFFICIAL_DISCLOSURE_PORTALS: dict[str, str] = {
    TENCENT.ticker: HKEX_TENCENT_URL,
    POP_MART.ticker: HKEX_POP_MART_URL,
    KWEICHOW_MOUTAI.ticker: SSE_MOUTAI_URL,
}


class OfficialDisclosurePortalProvider(DisclosurePortalProvider, DisclosureProvider):
    """Expose official disclosure search pages without claiming a complete scan.

    HKEXnews and SSE are authoritative entry points, but their HTML/query
    contracts may change and automated access must be reviewed before production
    use.  ``get_since`` therefore returns ``PORTAL_ONLY`` rather than an empty
    ``COMPLETE`` result.  This prevents downstream code from turning a connector
    limitation into the false statement "there were no new announcements".
    """

    provider_name = "official_disclosure_portal"

    def __init__(self, portals: Mapping[str, str] | None = None) -> None:
        self._portals = dict(portals or OFFICIAL_DISCLOSURE_PORTALS)

    def _source_url(self, security: Security) -> str:
        try:
            return self._portals[security.ticker]
        except KeyError as exc:
            raise UnsupportedSecurityError(
                f"no official disclosure portal configured for {security.ticker}"
            ) from exc

    def get_portal(
        self, security: Security, *, now: datetime | None = None
    ) -> DisclosurePortal:
        observed_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
        exchange = security.exchange.upper()
        notes = (
            "Official HKEXnews issuer title-search entry point."
            if exchange == "HKEX"
            else "Official Shanghai Stock Exchange company-announcement entry point."
        )
        return DisclosurePortal(
            security=security,
            source_url=self._source_url(security),
            observed_at=observed_at,
            freshness=Freshness.UNKNOWN,
            provisional=True,
            provider=self.provider_name,
            notes=notes,
        )
    def get_since(
        self,
        security: Security,
        *,
        since: datetime | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> ProviderBatch[Disclosure]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if since is not None:
            ensure_utc(since, field_name="since")
        portal = self.get_portal(security, now=now)
        return ProviderBatch(
            items=(),
            status=ProviderStatus.PORTAL_ONLY,
            source_url=portal.source_url,
            observed_at=portal.observed_at,
            fetched_at=portal.observed_at,
            freshness=Freshness.UNKNOWN,
            provisional=True,
            provider=self.provider_name,
            message=(
                "Official source is configured, but automated announcement extraction "
                "is not enabled; an empty item list does not mean no new disclosures."
            ),
        )


class StaticDisclosureProvider(DisclosureProvider):
    """Deterministic disclosure source for demos, fixtures, and manual imports."""

    provider_name = "static_disclosures"

    def __init__(
        self,
        disclosures: Iterable[Disclosure],
        *,
        portal_urls: Mapping[str, str] | None = None,
        max_age: timedelta = timedelta(days=30),
    ) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        grouped: dict[str, list[Disclosure]] = {}
        for disclosure in disclosures:
            grouped.setdefault(disclosure.security.ticker, []).append(disclosure)
        self._disclosures = {
            ticker: tuple(sorted(items, key=lambda item: item.published_at, reverse=True))
            for ticker, items in grouped.items()
        }
        self._portal_urls = dict(portal_urls or OFFICIAL_DISCLOSURE_PORTALS)
        self._max_age = max_age

    def get_since(
        self,
        security: Security,
        *,
        since: datetime | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> ProviderBatch[Disclosure]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        checked_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
        cutoff = ensure_utc(since, field_name="since") if since is not None else None
        records = self._disclosures.get(security.ticker, ())
        filtered = tuple(
            record
            for record in records
            if cutoff is None or record.published_at > cutoff
        )[:limit]
        latest_observation = max(
            (item.observed_at for item in filtered), default=checked_at
        )
        source_url = self._portal_urls.get(
            security.ticker,
            filtered[0].source_url if filtered else f"urn:static:{security.ticker}",
        )
        return ProviderBatch(
            items=filtered,
            status=ProviderStatus.COMPLETE,
            source_url=source_url,
            observed_at=latest_observation,
            fetched_at=checked_at,
            freshness=(
                classify_freshness(latest_observation, checked_at, self._max_age)
                if filtered
                else Freshness.UNKNOWN
            ),
            provisional=any(item.provisional for item in filtered),
            provider=self.provider_name,
            message="Complete for the supplied static snapshot only.",
        )
