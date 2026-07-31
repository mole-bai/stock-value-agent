"""Notification domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlparse


class NotificationPriority(IntEnum):
    P0 = 0  # hard risk / thesis invalidation
    P1 = 1  # new material filing or recommendation change
    P2 = 2  # valuation/price threshold
    P3 = 3  # routine calendar reminder / digest


@dataclass(frozen=True, slots=True)
class Notification:
    notification_id: str
    dedupe_key: str
    priority: NotificationPriority
    category: str
    title: str
    body: str
    created_at: datetime
    symbol: str | None = None
    source_url: str | None = None
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("notification_id", "dedupe_key", "category", "title", "body"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"notification requires {name}")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("notification created_at must be timezone-aware")
        if self.source_url:
            parsed = urlparse(self.source_url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("notification source_url must use http(s)")

    @property
    def fingerprint(self) -> str:
        stable = {
            "dedupe_key": self.dedupe_key,
            "priority": int(self.priority),
            "category": self.category,
            "title": self.title,
            "body": self.body,
            "symbol": self.symbol,
            "source_url": self.source_url,
            "attributes": dict(self.attributes or {}),
        }
        encoded = json.dumps(
            stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "dedupe_key": self.dedupe_key,
            "fingerprint": self.fingerprint,
            "priority": f"P{int(self.priority)}",
            "category": self.category,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
            "symbol": self.symbol,
            "source_url": self.source_url,
            "attributes": dict(self.attributes or {}),
        }


@dataclass(frozen=True, slots=True)
class NotificationDecision:
    send: bool
    reason: str
    deferred: bool = False
