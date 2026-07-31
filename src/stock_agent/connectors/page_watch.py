"""Generic byte-level watcher for official disclosure and investor-relations pages."""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from .base import ConnectorTransportError, OfficialPageProvider, UnsupportedSecurityError
from .disclosures import OFFICIAL_DISCLOSURE_PORTALS
from .models import (
    Freshness,
    OfficialPageSnapshot,
    Security,
    classify_freshness,
    ensure_utc,
)
from .transports import HttpResponse, Transport, response_body, response_url


HttpPageResponse = HttpResponse
PageTransport = Transport


def _default_page_transport(
    url: str, headers: Mapping[str, str], timeout: float
) -> HttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                body=response.read(),
                headers=dict(response.headers.items()),
                status=int(response.status),
                final_url=response.geturl(),
            )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorTransportError(f"official page request failed: {exc}") from exc


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value)
    return None


def fetch_official_page(
    url: str,
    *,
    security: Security | None = None,
    transport: PageTransport | None = None,
    now: datetime | None = None,
    timeout: float = 15.0,
    max_age: timedelta = timedelta(minutes=5),
    user_agent: str = "stock-agent-personal-research/0.1",
    provider_name: str = "official_page_watch",
) -> OfficialPageSnapshot:
    """GET an official page and return a provenance-rich content fingerprint."""

    if not url.strip():
        raise ValueError("url must not be empty")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    checked_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
    getter = transport or _default_page_transport
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": user_agent,
    }
    try:
        response = getter(url, headers, timeout)
    except ConnectorTransportError:
        raise
    except Exception as exc:
        raise ConnectorTransportError(f"official page request failed: {exc}") from exc
    if isinstance(response, bytes):
        response = HttpResponse(body=response, headers={}, final_url=url)
    if not 200 <= response.status < 300:
        raise ConnectorTransportError(
            f"official page returned HTTP status {response.status}"
        )
    source_url = response_url(response, url)
    digest = hashlib.sha256(response_body(response)).hexdigest()
    return OfficialPageSnapshot(
        security=security,
        source_url=source_url,
        content_hash=digest,
        etag=_header(response.headers, "etag"),
        last_modified=_header(response.headers, "last-modified"),
        observed_at=checked_at,
        fetched_at=checked_at,
        freshness=classify_freshness(checked_at, checked_at, max_age),
        provisional=True,
        provider=provider_name,
        http_status=response.status,
    )


class OfficialPageWatchProvider(OfficialPageProvider):
    """Watch configured official pages by security ticker."""

    provider_name = "official_page_watch"

    def __init__(
        self,
        pages: Mapping[str, str] | None = None,
        *,
        transport: PageTransport | None = None,
        timeout: float = 15.0,
        user_agent: str = "stock-agent-personal-research/0.1",
    ) -> None:
        self._pages = dict(pages or OFFICIAL_DISCLOSURE_PORTALS)
        self._transport = transport
        self._timeout = timeout
        self._user_agent = user_agent

    def get_snapshot(
        self, security: Security, *, now: datetime | None = None
    ) -> OfficialPageSnapshot:
        try:
            url = self._pages[security.ticker]
        except KeyError as exc:
            raise UnsupportedSecurityError(
                f"no official page configured for {security.ticker}"
            ) from exc
        return fetch_official_page(
            url,
            security=security,
            transport=self._transport,
            now=now,
            timeout=self._timeout,
            user_agent=self._user_agent,
            provider_name=self.provider_name,
        )
