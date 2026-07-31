"""Tencent Finance quote adapter for the personal three-stock watchlist."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .base import (
    ConnectorDataError,
    ConnectorTransportError,
    QuoteProvider,
    UnsupportedSecurityError,
)
from .models import Quote, Security, classify_freshness, ensure_utc
from .transports import Transport, response_body, response_url


TENCENT_QUOTE_SYMBOLS: dict[str, str] = {
    "0700.HK": "r_hk00700",
    "9992.HK": "r_hk09992",
    "600519.SS": "sh600519",
}

_LINE = re.compile(r'^v_(?P<symbol>[A-Za-z0-9_]+)="(?P<data>.*)";$')
_CHINA_TIME = timezone(timedelta(hours=8))


def _default_http_get(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorTransportError(f"Tencent quote request failed: {exc}") from exc


def _number(value: str, field_name: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError) as exc:
        raise ConnectorDataError(
            f"invalid Tencent quote {field_name}: {value!r}"
        ) from exc


def _integer(value: str, field_name: str) -> int:
    number = _number(value, field_name)
    if number != number.to_integral_value():
        raise ConnectorDataError(
            f"invalid Tencent quote {field_name}: {value!r}"
        )
    return int(number)


def _market_time(value: str) -> datetime:
    cleaned = value.strip()
    for pattern in ("%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return (
                datetime.strptime(cleaned, pattern)
                .replace(tzinfo=_CHINA_TIME)
                .astimezone(timezone.utc)
            )
        except ValueError:
            pass
    raise ConnectorDataError(f"invalid Tencent quote timestamp: {value!r}")


class TencentQuoteProvider(QuoteProvider):
    """Fetch lightweight snapshots from Tencent Finance.

    This remains an unlicensed personal-prototype source.  Every observation is
    explicitly provisional and should be replaced before public redistribution.
    """

    provider_name = "tencent_quotes_personal_prototype"
    base_url = "https://qt.gtimg.cn/q="
    terms_note = (
        "Personal prototype source; confirm access and market-data rights and "
        "replace with a licensed feed before production or redistribution."
    )

    def __init__(
        self,
        *,
        symbols: Mapping[str, str] | None = None,
        transport: Transport | None = None,
        timeout: float = 8.0,
        max_age: timedelta = timedelta(days=4),
        user_agent: str = "stock-agent-personal-prototype/0.2",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._symbols = dict(symbols or TENCENT_QUOTE_SYMBOLS)
        self._transport = transport or _default_http_get
        self._timeout = timeout
        self._max_age = max_age
        self._headers = {
            "Accept": "text/plain,*/*;q=0.8",
            "Referer": "https://gu.qq.com/",
            "User-Agent": user_agent,
        }

    def get_latest(self, security: Security, *, now: datetime | None = None) -> Quote:
        return self.get_many((security,), now=now)[0]

    def get_many(
        self, securities: Iterable[Security], *, now: datetime | None = None
    ) -> tuple[Quote, ...]:
        requested = tuple(securities)
        if not requested:
            return ()
        checked_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
        symbols: list[str] = []
        for security in requested:
            try:
                symbols.append(self._symbols[security.ticker])
            except KeyError as exc:
                raise UnsupportedSecurityError(
                    f"no Tencent quote symbol configured for {security.ticker}"
                ) from exc
        requested_url = self.base_url + ",".join(symbols)
        try:
            response = self._transport(requested_url, self._headers, self._timeout)
        except ConnectorTransportError:
            raise
        except Exception as exc:
            raise ConnectorTransportError(
                f"Tencent quote request failed: {exc}"
            ) from exc
        try:
            text = response_body(response).decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ConnectorDataError(
                "Tencent quote response is not valid GB18030"
            ) from exc
        records = self._parse_records(text)
        source_url = response_url(response, requested_url)
        quotes: list[Quote] = []
        for security, symbol in zip(requested, symbols, strict=True):
            fields = records.get(symbol)
            if fields is None or not any(field.strip() for field in fields):
                raise ConnectorDataError(
                    f"Tencent quote response is missing {symbol}"
                )
            quotes.append(
                self._parse_quote(
                    security,
                    symbol,
                    fields,
                    source_url=source_url,
                    checked_at=checked_at,
                )
            )
        return tuple(quotes)

    @staticmethod
    def _parse_records(text: str) -> dict[str, list[str]]:
        records: dict[str, list[str]] = {}
        for raw_line in text.replace("\r", "").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            match = _LINE.fullmatch(line)
            if match is None:
                raise ConnectorDataError(
                    f"malformed Tencent quote line: {line[:80]!r}"
                )
            records[match.group("symbol")] = match.group("data").split("~")
        if not records:
            raise ConnectorDataError("Tencent quote response is empty")
        return records

    def _parse_quote(
        self,
        security: Security,
        symbol: str,
        fields: list[str],
        *,
        source_url: str,
        checked_at: datetime,
    ) -> Quote:
        if symbol.startswith("r_hk"):
            if len(fields) < 39:
                raise ConnectorDataError(
                    f"Tencent HK quote has only {len(fields)} fields"
                )
            observed_at = _market_time(fields[30])
            currency = "HKD"
            price = _number(fields[3], "last")
            previous_close = _number(fields[4], "previous_close")
            open_price = _number(fields[5], "open")
            high = _number(fields[33], "high")
            low = _number(fields[34], "low")
            volume = _integer(fields[6], "volume")
            turnover = _number(fields[38], "turnover")
            bid = ask = None
        elif symbol.startswith(("sh", "sz", "bj")):
            if len(fields) < 38:
                raise ConnectorDataError(
                    f"Tencent A-share quote has only {len(fields)} fields"
                )
            observed_at = _market_time(fields[30])
            currency = "CNY"
            price = _number(fields[3], "last")
            previous_close = _number(fields[4], "previous_close")
            open_price = _number(fields[5], "open")
            high = _number(fields[33], "high")
            low = _number(fields[34], "low")
            volume = _integer(fields[36], "volume_lots") * 100
            trade_summary = fields[35].split("/")
            turnover = (
                _number(trade_summary[2], "turnover")
                if len(trade_summary) >= 3
                else None
            )
            bid = _number(fields[11], "bid")
            ask = _number(fields[21], "ask")
        else:
            raise ConnectorDataError(
                f"unsupported Tencent quote market symbol: {symbol}"
            )
        return Quote(
            security=security,
            price=price,
            currency=currency,
            observed_at=observed_at,
            fetched_at=checked_at,
            source_url=source_url,
            freshness=classify_freshness(observed_at, checked_at, self._max_age),
            provisional=True,
            provider=self.provider_name,
            previous_close=previous_close,
            open=open_price,
            high=high,
            low=low,
            bid=bid,
            ask=ask,
            turnover=turnover,
            volume=volume,
            raw_symbol=symbol,
        )


__all__ = ["TENCENT_QUOTE_SYMBOLS", "TencentQuoteProvider"]
