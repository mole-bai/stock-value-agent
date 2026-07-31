"""Interfaces and errors shared by connector implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from .models import (
    Disclosure,
    DisclosurePortal,
    OfficialPageSnapshot,
    ProviderBatch,
    Quote,
    Security,
)


class ConnectorError(RuntimeError):
    """Base class for recoverable provider failures."""


class ConnectorTransportError(ConnectorError):
    """The provider could not be reached or returned an HTTP failure."""


class ConnectorDataError(ConnectorError):
    """The provider response did not satisfy the connector's data contract."""


class UnsupportedSecurityError(ConnectorError):
    """The provider has no mapping for the requested security."""


class QuoteProvider(ABC):
    """Replaceable source for latest market observations."""

    @abstractmethod
    def get_latest(self, security: Security, *, now: datetime | None = None) -> Quote:
        raise NotImplementedError

    def get_many(
        self, securities: Iterable[Security], *, now: datetime | None = None
    ) -> tuple[Quote, ...]:
        return tuple(self.get_latest(security, now=now) for security in securities)


class DisclosureProvider(ABC):
    """Replaceable source for issuer filings and announcements."""

    @abstractmethod
    def get_since(
        self,
        security: Security,
        *,
        since: datetime | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> ProviderBatch[Disclosure]:
        raise NotImplementedError


class DisclosurePortalProvider(ABC):
    """Maps a security to an official disclosure search page."""

    @abstractmethod
    def get_portal(
        self, security: Security, *, now: datetime | None = None
    ) -> DisclosurePortal:
        raise NotImplementedError


class OfficialPageProvider(ABC):
    """Captures byte-level snapshots of an official page."""

    @abstractmethod
    def get_snapshot(
        self, security: Security, *, now: datetime | None = None
    ) -> OfficialPageSnapshot:
        raise NotImplementedError
