"""Provider-neutral data contracts for market and disclosure connectors.

The connector layer deliberately keeps provenance on every returned object.  A
caller should never have to infer whether a timestamp is the market timestamp
or the time at which the connector happened to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar


class Freshness(str, Enum):
    """Age classification at the moment a provider result was created."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ProviderStatus(str, Enum):
    """Whether an empty batch can safely be understood as no new records."""

    COMPLETE = "complete"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    PORTAL_ONLY = "portal_only"


def ensure_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    """Return an aware datetime converted to UTC.

    Silent interpretation of a naive datetime is unsafe for cross-market data,
    so connectors reject it instead.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def classify_freshness(
    observed_at: datetime | None,
    checked_at: datetime,
    max_age: timedelta,
    *,
    future_tolerance: timedelta = timedelta(minutes=5),
) -> Freshness:
    """Classify an observation without pretending future-dated data is fresh."""

    if observed_at is None:
        return Freshness.UNKNOWN
    observed_at = ensure_utc(observed_at, field_name="observed_at")
    checked_at = ensure_utc(checked_at, field_name="checked_at")
    age = checked_at - observed_at
    if age < -future_tolerance:
        return Freshness.UNKNOWN
    return Freshness.FRESH if age <= max_age else Freshness.STALE


@dataclass(frozen=True, slots=True)
class Security:
    """A listed security, kept separate from the corporate issuer."""

    ticker: str
    market: str
    exchange: str
    issuer_id: str
    name: str

    def __post_init__(self) -> None:
        for name in ("ticker", "market", "exchange", "issuer_id", "name"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")

    @property
    def key(self) -> str:
        return f"{self.market}:{self.exchange}:{self.ticker}:{self.issuer_id}"


@dataclass(frozen=True, slots=True)
class Quote:
    """Latest price observation returned by a quote provider."""

    security: Security
    price: Decimal
    currency: str
    observed_at: datetime
    fetched_at: datetime
    source_url: str
    freshness: Freshness
    provisional: bool
    provider: str
    previous_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    turnover: Decimal | None = None
    volume: int | None = None
    raw_symbol: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", ensure_utc(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(
            self, "fetched_at", ensure_utc(self.fetched_at, field_name="fetched_at")
        )
        if self.price < 0:
            raise ValueError("price must not be negative")
        if not self.currency.strip():
            raise ValueError("currency must not be empty")
        if not self.source_url.strip():
            raise ValueError("source_url must not be empty")


@dataclass(frozen=True, slots=True)
class Disclosure:
    """A filing or announcement discovered by a disclosure provider."""

    security: Security
    disclosure_id: str
    title: str
    category: str
    published_at: datetime
    observed_at: datetime
    fetched_at: datetime
    source_url: str
    freshness: Freshness
    provisional: bool
    provider: str
    language: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "published_at", ensure_utc(self.published_at, field_name="published_at")
        )
        object.__setattr__(
            self, "observed_at", ensure_utc(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(
            self, "fetched_at", ensure_utc(self.fetched_at, field_name="fetched_at")
        )
        for name in ("disclosure_id", "title", "category", "source_url", "provider"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderBatch(Generic[T]):
    """A provider scan result, including explicit completeness semantics."""

    items: tuple[T, ...]
    status: ProviderStatus
    source_url: str
    observed_at: datetime
    freshness: Freshness
    provisional: bool
    provider: str
    message: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", ensure_utc(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(
            self, "fetched_at", ensure_utc(self.fetched_at, field_name="fetched_at")
        )
        if not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if self.status is not ProviderStatus.COMPLETE and not self.provisional:
            raise ValueError("an incomplete provider batch must be provisional")


@dataclass(frozen=True, slots=True)
class DisclosurePortal:
    """Official search entry point when safe automated extraction is unavailable."""

    security: Security
    source_url: str
    observed_at: datetime
    freshness: Freshness
    provisional: bool
    provider: str
    notes: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", ensure_utc(self.observed_at, field_name="observed_at")
        )
        if not self.source_url.strip():
            raise ValueError("source_url must not be empty")


@dataclass(frozen=True, slots=True)
class OfficialPageSnapshot:
    """Byte-level observation of an official disclosure or IR index page.

    The hash detects a page change; it is not evidence that a specific filing
    was published.  Downstream orchestration should turn a changed hash into a
    pending-review event until a disclosure parser confirms the semantic change.
    """

    source_url: str
    content_hash: str
    observed_at: datetime
    fetched_at: datetime
    freshness: Freshness
    provisional: bool
    provider: str
    etag: str | None = None
    last_modified: str | None = None
    http_status: int = 200
    security: Security | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", ensure_utc(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(
            self, "fetched_at", ensure_utc(self.fetched_at, field_name="fetched_at")
        )
        if not self.source_url.strip():
            raise ValueError("source_url must not be empty")
        if len(self.content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_hash.lower()
        ):
            raise ValueError("content_hash must be a SHA-256 hex digest")
        if not 200 <= self.http_status < 300:
            raise ValueError("http_status must be a successful HTTP status")
