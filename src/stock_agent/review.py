"""Persistent human-review queue for newly discovered official disclosures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, MutableMapping


class ReviewDecision(str, Enum):
    MATERIAL = "material"
    NON_MATERIAL = "non_material"
    DATA_UPDATE_REQUIRED = "data_update_required"
    DUPLICATE = "duplicate"
    UPDATED_AND_REVALUED = "updated_and_revalued"


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_id: str
    symbol: str
    document_id: str
    title: str
    published_at: str | None
    source_url: str
    discovered_at: str
    status: str = "pending"
    decision: str | None = None
    note: str | None = None
    reviewed_at: str | None = None
    history: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReviewItem":
        return cls(
            review_id=str(value.get("review_id", "")),
            symbol=str(value.get("symbol", "")),
            document_id=str(value.get("document_id", "")),
            title=str(value.get("title", "")),
            published_at=str(value["published_at"]) if value.get("published_at") else None,
            source_url=str(value.get("source_url", "")),
            discovered_at=str(value.get("discovered_at", "")),
            status=str(value.get("status", "pending")),
            decision=str(value["decision"]) if value.get("decision") else None,
            note=str(value["note"]) if value.get("note") else None,
            reviewed_at=str(value["reviewed_at"]) if value.get("reviewed_at") else None,
            history=tuple(
                dict(item)
                for item in value.get("history", ())
                if isinstance(item, Mapping)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value = {name: getattr(self, name) for name in self.__dataclass_fields__}
        value["history"] = [dict(item) for item in self.history]
        return value


class ReviewQueue:
    """Operate on caller-owned state; the orchestration store persists it."""

    def __init__(self, state: MutableMapping[str, Any]) -> None:
        self.state = state
        self.records: MutableMapping[str, Any] = state.setdefault("review_queue", {})

    def enqueue(
        self,
        *,
        symbol: str,
        document_id: str,
        title: str,
        source_url: str,
        discovered_at: datetime,
        published_at: str | None = None,
    ) -> ReviewItem:
        if discovered_at.tzinfo is None or discovered_at.utcoffset() is None:
            raise ValueError("discovered_at must be timezone-aware")
        review_id = f"{symbol}:{document_id}"
        existing = self.records.get(review_id)
        if isinstance(existing, Mapping):
            return ReviewItem.from_mapping(existing)
        item = ReviewItem(
            review_id=review_id,
            symbol=symbol,
            document_id=document_id,
            title=title,
            published_at=published_at,
            source_url=source_url,
            discovered_at=discovered_at.isoformat(),
        )
        self.records[review_id] = item.to_dict()
        return item

    def pending(self, *, symbol: str | None = None) -> tuple[ReviewItem, ...]:
        return self.items(symbol=symbol, include_resolved=False)

    def items(
        self, *, symbol: str | None = None, include_resolved: bool = True
    ) -> tuple[ReviewItem, ...]:
        items = (
            ReviewItem.from_mapping(value)
            for value in self.records.values()
            if isinstance(value, Mapping)
        )
        return tuple(
            sorted(
                (
                    item
                    for item in items
                    if (include_resolved or item.status in {"pending", "action_required"})
                    and (symbol is None or item.symbol == symbol)
                ),
                key=lambda item: (item.discovered_at, item.review_id),
            )
        )

    def resolve(
        self,
        review_id: str,
        *,
        decision: ReviewDecision | str,
        reviewed_at: datetime,
        note: str | None = None,
    ) -> ReviewItem:
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        raw = self.records.get(review_id)
        if not isinstance(raw, Mapping):
            raise KeyError(f"unknown review item: {review_id}")
        parsed_decision = (
            decision if isinstance(decision, ReviewDecision) else ReviewDecision(decision)
        )
        current = ReviewItem.from_mapping(raw)
        resolved = ReviewItem(
            review_id=current.review_id,
            symbol=current.symbol,
            document_id=current.document_id,
            title=current.title,
            published_at=current.published_at,
            source_url=current.source_url,
            discovered_at=current.discovered_at,
            status=(
                "action_required"
                if parsed_decision
                in {ReviewDecision.MATERIAL, ReviewDecision.DATA_UPDATE_REQUIRED}
                else "reviewed"
            ),
            decision=parsed_decision.value,
            note=note.strip() if note and note.strip() else None,
            reviewed_at=reviewed_at.isoformat(),
            history=(
                *current.history,
                {
                    "decision": parsed_decision.value,
                    "note": note.strip() if note and note.strip() else None,
                    "reviewed_at": reviewed_at.isoformat(),
                },
            ),
        )
        self.records[review_id] = resolved.to_dict()
        return resolved

    def blocks_positive_view(self, symbol: str) -> bool:
        return bool(self.pending(symbol=symbol))
