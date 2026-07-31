"""Offline quote provider useful for deterministic runs and tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable

from .base import QuoteProvider, UnsupportedSecurityError
from .models import Quote, Security, ensure_utc


class StaticQuoteProvider(QuoteProvider):
    provider_name = "static_quotes"

    def __init__(self, quotes: Iterable[Quote]) -> None:
        self._quotes = {quote.security.ticker: quote for quote in quotes}

    def get_latest(self, security: Security, *, now: datetime | None = None) -> Quote:
        try:
            quote = self._quotes[security.ticker]
        except KeyError as exc:
            raise UnsupportedSecurityError(
                f"no static quote configured for {security.ticker}"
            ) from exc
        fetched_at = ensure_utc(now or datetime.now(timezone.utc), field_name="now")
        return replace(quote, fetched_at=fetched_at)
