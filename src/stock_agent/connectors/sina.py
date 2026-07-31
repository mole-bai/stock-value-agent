"""Sina quote adapter for the three-stock personal prototype watchlist."""

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


SINA_SYMBOLS: dict[str, str] = {
    "0700.HK": "hk00700",
    "9992.HK": "hk09992",
    "600519.SS": "sh600519",
}

_LINE = re.compile(r'^var\s+hq_str_(?P<symbol>[A-Za-z0-9]+)="(?P<data>.*)";$')
_CHINA_TIME = timezone(timedelta(hours=8))


def _default_http_get(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorTransportError(f"Sina quote request failed: {exc}") from exc


def _number(value: str, field_name: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError) as exc:
        raise ConnectorDataError(f"invalid Sina {field_name}: {value!r}") from exc


def _integer(value: str, field_name: str) -> int:
    number = _number(value, field_name)
    if number != number.to_integral_value():
        raise ConnectorDataError(f"invalid Sina {field_name}: {value!r}")
    return int(number)


def _market_time(date_value: str, time_value: str) -> datetime:
    value = f"{date_value.strip()} {time_value.strip()}"
    for pattern in (
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=_CHINA_TIME).astimezone(
                timezone.utc
            )
        except ValueError:
            pass
    raise ConnectorDataError(f"invalid Sina market timestamp: {value!r}")


class SinaQuoteProvider(QuoteProvider):
    """Fetch Sina's lightweight HK/A-share snapshots.

    This is a clearly labelled personal prototype source, not a licensed
    exchange feed.  Every returned quote is provisional.
    """

    provider_name = "sina_quotes_personal_prototype"
    base_url = "https://hq.sinajs.cn/list="
    terms_note = (
        "Personal prototype source; confirm access and market-data rights and "
        "replace with a licensed feed before production or redistribution."
    )

    def __init__(
        self,
        *,
        symbols: Mapping[str, str] | None = None,
        transport: Transport | None = None,
        timeout: float = 10.0,
        max_age: timedelta = timedelta(days=4),
        user_agent: str = "stock-agent-personal-prototype/0.1",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._symbols = dict(symbols or SINA_SYMBOLS)
        self._transport = transport or _default_http_get
        self._timeout = timeout
        self._max_age = max_age
        self._headers = {
            "Accept": "text/plain,*/*;q=0.8",
            "Referer": "https://finance.sina.com.cn/",
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
        source_symbols: list[str] = []
        for security in requested:
            try:
                source_symbols.append(self._symbols[security.ticker])
            except KeyError as exc:
                raise UnsupportedSecurityError(
                    f"no Sina symbol configured for {security.ticker}"
                ) from exc
        requested_url = self.base_url + ",".join(source_symbols)
        try:
            response = self._transport(
                requested_url, self._headers, self._timeout
            )
        except ConnectorTransportError:
            raise
        except Exception as exc:
            raise ConnectorTransportError(f"Sina quote request failed: {exc}") from exc
        try:
            text = response_body(response).decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ConnectorDataError("Sina quote response is not valid GB18030") from exc
        records = self._parse_records(text)
        source_url = response_url(response, requested_url)
        quotes: list[Quote] = []
        for security, symbol in zip(requested, source_symbols, strict=True):
            fields = records.get(symbol)
            if fields is None or not any(field.strip() for field in fields):
                raise ConnectorDataError(f"Sina quote response is missing {symbol}")
            quotes.append(
                self._parse_quote(
                    security, symbol, fields, source_url=source_url, checked_at=checked_at
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
                raise ConnectorDataError(f"malformed Sina quote line: {line[:80]!r}")
            records[match.group("symbol")] = match.group("data").split(",")
        if not records:
            raise ConnectorDataError("Sina quote response is empty")
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
        if symbol.startswith("hk"):
            if len(fields) < 19:
                raise ConnectorDataError(f"Sina HK quote has only {len(fields)} fields")
            open_index, previous_index, high_index, low_index, last_index = 2, 3, 4, 5, 6
            bid_index, ask_index, amount_index, volume_index = 9, 10, 11, 12
            date_index, time_index = 17, 18
            currency = "HKD"
        elif symbol.startswith(("sh", "sz", "bj")):
            if len(fields) < 32:
                raise ConnectorDataError(f"Sina A-share quote has only {len(fields)} fields")
            open_index, previous_index, last_index, high_index, low_index = 1, 2, 3, 4, 5
            bid_index, ask_index, volume_index, amount_index = 6, 7, 8, 9
            date_index, time_index = 30, 31
            currency = "CNY"
        else:
            raise ConnectorDataError(f"unsupported Sina market symbol: {symbol}")
        observed_at = _market_time(fields[date_index], fields[time_index])
        return Quote(
            security=security,
            price=_number(fields[last_index], "last"),
            currency=currency,
            observed_at=observed_at,
            fetched_at=checked_at,
            source_url=source_url,
            freshness=classify_freshness(observed_at, checked_at, self._max_age),
            provisional=True,
            provider=self.provider_name,
            previous_close=_number(fields[previous_index], "previous_close"),
            open=_number(fields[open_index], "open"),
            high=_number(fields[high_index], "high"),
            low=_number(fields[low_index], "low"),
            bid=_number(fields[bid_index], "bid"),
            ask=_number(fields[ask_index], "ask"),
            turnover=_number(fields[amount_index], "amount"),
            volume=_integer(fields[volume_index], "volume"),
            raw_symbol=symbol,
        )
