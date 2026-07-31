"""Data contracts for semantic monitoring of official issuer events.

The contracts intentionally distinguish "a list was extracted" from "the
official portal was reachable".  In particular, an empty HTML extraction is
never represented as a successful scan: callers therefore cannot turn a
parser limitation into the assertion that an issuer published no notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit


PARSER_VERSION = "official_events.v1"


class SourceKind(str, Enum):
    """The coverage role and response contract of an official source."""

    IR_INDEX = "ir_index"
    EXCHANGE_PORTAL = "exchange_portal"
    STRUCTURED_API = "structured_api"


class EventScanStatus(str, Enum):
    """Outcome of one official-page semantic extraction."""

    EXTRACTED = "extracted"
    PORTAL_ONLY = "portal_only"
    DEGRADED = "degraded"


class EventDiffStatus(str, Enum):
    """How two semantic snapshots relate."""

    BASELINE = "baseline"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNKNOWN = "unknown"


def ensure_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _validate_http_url(value: str, *, field_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


@dataclass(frozen=True, slots=True)
class OfficialEventSource:
    """One authoritative announcement source monitored for an issuer."""

    ticker: str
    source_id: str
    label: str
    url: str
    kind: SourceKind
    language: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("ticker", "source_id", "label"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        _validate_http_url(self.url, field_name="url")


@dataclass(frozen=True, slots=True)
class OfficialEvent:
    """A normalized announcement or report link found on an official page."""

    ticker: str
    source_id: str
    document_id: str
    title: str
    document_url: str
    published_date: date | None = None

    def __post_init__(self) -> None:
        for field_name in ("ticker", "source_id", "document_id", "title"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        _validate_http_url(self.document_url, field_name="document_url")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "source_id": self.source_id,
            "document_id": self.document_id,
            "title": self.title,
            "document_url": self.document_url,
            "published_date": (
                self.published_date.isoformat() if self.published_date else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OfficialEvent":
        raw_date = value.get("published_date")
        return cls(
            ticker=str(value["ticker"]),
            source_id=str(value["source_id"]),
            document_id=str(value["document_id"]),
            title=str(value["title"]),
            document_url=str(value["document_url"]),
            published_date=date.fromisoformat(str(raw_date)) if raw_date else None,
        )


@dataclass(frozen=True, slots=True)
class SemanticEventSnapshot:
    """Normalized view of one official event source at a point in time.

    ``EXTRACTED`` means recognizable records were found.  It does not promise
    that the source exposes every issuer announcement, so ``coverage_complete``
    defaults to false and downstream wording should say "no detected semantic
    change", never "no announcements".
    """

    ticker: str
    source_id: str
    source_label: str
    source_url: str
    status: EventScanStatus
    events: tuple[OfficialEvent, ...]
    semantic_hash: str | None
    observed_at: datetime
    fetched_at: datetime
    message: str
    provisional: bool = True
    coverage_complete: bool = False
    parser_version: str = PARSER_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field_name="observed_at"),
        )
        object.__setattr__(
            self,
            "fetched_at",
            ensure_utc(self.fetched_at, field_name="fetched_at"),
        )
        for field_name in ("ticker", "source_id", "source_label", "message"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        _validate_http_url(self.source_url, field_name="source_url")
        if self.status is EventScanStatus.EXTRACTED:
            if not self.events:
                raise ValueError("an extracted snapshot must contain at least one event")
            if self.semantic_hash is None:
                raise ValueError("an extracted snapshot requires semantic_hash")
        elif self.semantic_hash is not None:
            raise ValueError("a non-extracted snapshot cannot carry semantic_hash")
        if self.semantic_hash is not None and (
            len(self.semantic_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.semantic_hash)
        ):
            raise ValueError("semantic_hash must be a lowercase SHA-256 digest")
        if any(event.ticker != self.ticker for event in self.events):
            raise ValueError("all events must belong to the snapshot ticker")
        if any(event.source_id != self.source_id for event in self.events):
            raise ValueError("all events must belong to the snapshot source")
        if self.coverage_complete and self.status is not EventScanStatus.EXTRACTED:
            raise ValueError("only an extracted snapshot can claim complete coverage")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "source_url": self.source_url,
            "status": self.status.value,
            "events": [event.to_dict() for event in self.events],
            "semantic_hash": self.semantic_hash,
            "observed_at": self.observed_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "message": self.message,
            "provisional": self.provisional,
            "coverage_complete": self.coverage_complete,
            "parser_version": self.parser_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticEventSnapshot":
        raw_events = value.get("events", ())
        if not isinstance(raw_events, (list, tuple)):
            raise ValueError("events must be a list")
        return cls(
            ticker=str(value["ticker"]),
            source_id=str(value["source_id"]),
            source_label=str(value["source_label"]),
            source_url=str(value["source_url"]),
            status=EventScanStatus(str(value["status"])),
            events=tuple(OfficialEvent.from_dict(item) for item in raw_events),
            semantic_hash=(
                str(value["semantic_hash"])
                if value.get("semantic_hash") is not None
                else None
            ),
            observed_at=datetime.fromisoformat(str(value["observed_at"])),
            fetched_at=datetime.fromisoformat(str(value["fetched_at"])),
            message=str(value["message"]),
            provisional=bool(value.get("provisional", True)),
            coverage_complete=bool(value.get("coverage_complete", False)),
            parser_version=str(value.get("parser_version", PARSER_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class EventSnapshotDiff:
    """Pure comparison result between persisted and current snapshots."""

    ticker: str
    source_id: str
    status: EventDiffStatus
    is_baseline: bool
    new_events: tuple[OfficialEvent, ...]
    updated_events: tuple[OfficialEvent, ...]
    removed_document_ids: tuple[str, ...]
    previous_hash: str | None
    current_hash: str | None
    message: str

    @property
    def has_detected_change(self) -> bool:
        return self.status is EventDiffStatus.CHANGED
