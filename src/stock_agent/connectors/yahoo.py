"""Yahoo Finance chart adapter for a personal, non-production prototype.

Yahoo is not an official exchange feed.  Results from this provider therefore
remain ``provisional=True`` even after a daily candle appears complete.  The
adapter uses only the Python standard library and exposes an injected transport
so tests and deployments do not need to monkeypatch global networking.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from .base import ConnectorDataError, ConnectorTransportError, QuoteProvider
from .models import Quote, Security, classify_freshness, ensure_utc
from .transports import HttpResponse, Transport, response_body, response_url


HttpGet = Transport


def _default_http_get(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorTransportError(f"Yahoo chart request failed: {exc}") from exc


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConnectorDataError(f"invalid decimal value: {value!r}") from exc


def _at(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


class YahooChartQuoteProvider(QuoteProvider):
    """Fetch the latest non-null daily candle from Yahoo's chart endpoint."""

    provider_name = "yahoo_chart_personal_prototype"
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"
    terms_note = (
        "Personal prototype source; not an official or licensed exchange feed. "
        "Validate licensing and replace before redistribution or production use."
    )

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        timeout: float = 10.0,
        max_age: timedelta = timedelta(days=4),
        user_agent: str = "stock-agent-personal-prototype/0.1",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._http_get = http_get or _default_http_get
        self._timeout = timeout
        self._max_age = max_age
        self._headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }

    def _url(self, ticker: str) -> str:
        symbol = urllib.parse.quote(ticker, safe="")
        query = urllib.parse.urlencode(
            {
                "interval": "1d",
                "range": "5d",
                "events": "div,splits",
                "includePrePost": "false",
            }
        )
        return f"{self.base_url}/{symbol}?{query}"

    def get_latest(self, security: Security, *, now: datetime | None = None) -> Quote:
        checked_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
        source_url = self._url(security.ticker)
        try:
            response = self._http_get(source_url, self._headers, self._timeout)
        except ConnectorTransportError:
            raise
        except Exception as exc:  # injected transports may use their own errors
            raise ConnectorTransportError(f"Yahoo chart request failed: {exc}") from exc

        try:
            document = json.loads(response_body(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorDataError("Yahoo chart response was not valid UTF-8 JSON") from exc

        chart = document.get("chart") if isinstance(document, dict) else None
        if not isinstance(chart, dict):
            raise ConnectorDataError("Yahoo chart response is missing chart")
        if chart.get("error"):
            error = chart["error"]
            description = error.get("description") if isinstance(error, dict) else str(error)
            raise ConnectorDataError(f"Yahoo chart error: {description}")
        results = chart.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise ConnectorDataError("Yahoo chart response has no result")

        result = results[0]
        timestamps = result.get("timestamp")
        indicators = result.get("indicators")
        quote_groups = indicators.get("quote") if isinstance(indicators, dict) else None
        if not isinstance(timestamps, list) or not isinstance(quote_groups, list) or not quote_groups:
            raise ConnectorDataError("Yahoo chart response has no daily candles")
        candle = quote_groups[0]
        if not isinstance(candle, dict):
            raise ConnectorDataError("Yahoo chart daily candle is malformed")
        closes = candle.get("close")
        if not isinstance(closes, list):
            raise ConnectorDataError("Yahoo chart response has no close series")

        selected_index: int | None = None
        for index in range(min(len(timestamps), len(closes)) - 1, -1, -1):
            if timestamps[index] is not None and closes[index] is not None:
                selected_index = index
                break
        if selected_index is None:
            raise ConnectorDataError("Yahoo chart response has no usable price observation")

        try:
            observed_at = datetime.fromtimestamp(
                int(timestamps[selected_index]), tz=timezone.utc
            )
        except (ValueError, TypeError, OSError, OverflowError) as exc:
            raise ConnectorDataError("Yahoo chart observation timestamp is invalid") from exc

        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        currency = meta.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            raise ConnectorDataError("Yahoo chart response has no currency")

        previous_close = None
        for index in range(selected_index - 1, -1, -1):
            candidate = _at(closes, index)
            if candidate is not None:
                previous_close = _decimal(candidate)
                break
        if previous_close is None:
            previous_close = _decimal(
                meta.get("chartPreviousClose", meta.get("previousClose"))
            )

        return Quote(
            security=security,
            price=_decimal(closes[selected_index]) or Decimal("0"),
            currency=currency.upper(),
            observed_at=observed_at,
            fetched_at=checked_at,
            source_url=response_url(response, source_url),
            freshness=classify_freshness(observed_at, checked_at, self._max_age),
            provisional=True,
            provider=self.provider_name,
            previous_close=previous_close,
            open=_decimal(_at(candle.get("open"), selected_index)),
            high=_decimal(_at(candle.get("high"), selected_index)),
            low=_decimal(_at(candle.get("low"), selected_index)),
            volume=(
                int(_at(candle.get("volume"), selected_index))
                if _at(candle.get("volume"), selected_index) is not None
                else None
            ),
            raw_symbol=str(meta.get("symbol") or security.ticker),
        )
