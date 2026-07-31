"""Pure baseline and change detection for semantic event snapshots."""

from __future__ import annotations

from .models import (
    EventDiffStatus,
    EventScanStatus,
    EventSnapshotDiff,
    OfficialEvent,
    SemanticEventSnapshot,
)


def diff_event_snapshots(
    previous: SemanticEventSnapshot | None,
    current: SemanticEventSnapshot,
) -> EventSnapshotDiff:
    """Compare two snapshots without mutating either snapshot or external state."""

    if previous is not None and (
        previous.ticker != current.ticker or previous.source_id != current.source_id
    ):
        raise ValueError("snapshots must have the same ticker and source_id")

    if current.status is not EventScanStatus.EXTRACTED:
        return _diff(
            current,
            status=EventDiffStatus.UNKNOWN,
            previous=previous,
            message=(
                "当前官方页面未形成可比较的公告语义列表；空提取结果不表示没有新公告。"
            ),
        )

    if (
        previous is None
        or previous.status is not EventScanStatus.EXTRACTED
        or previous.semantic_hash is None
        or previous.parser_version != current.parser_version
    ):
        return _diff(
            current,
            status=EventDiffStatus.BASELINE,
            previous=previous,
            is_baseline=True,
            message="已建立公告语义基线；基线内历史公告不会被误报为新增。",
        )

    if previous.semantic_hash == current.semantic_hash:
        return _diff(
            current,
            status=EventDiffStatus.UNCHANGED,
            previous=previous,
            message="未检测到公告列表语义变化；这不等同于对全市场断言没有公告。",
        )

    old_by_id = {event.document_id: event for event in previous.events}
    new_by_id = {event.document_id: event for event in current.events}
    new_events = tuple(
        event for event in current.events if event.document_id not in old_by_id
    )
    updated_events = tuple(
        event
        for event in current.events
        if event.document_id in old_by_id
        and _event_semantics(event) != _event_semantics(old_by_id[event.document_id])
    )
    removed_ids = tuple(sorted(set(old_by_id) - set(new_by_id)))
    if not new_events and not updated_events and not removed_ids:
        return _diff(
            current,
            status=EventDiffStatus.UNKNOWN,
            previous=previous,
            message="语义哈希发生变化但记录差异无法解释，需人工复核。",
        )
    return _diff(
        current,
        status=EventDiffStatus.CHANGED,
        previous=previous,
        new_events=new_events,
        updated_events=updated_events,
        removed_document_ids=removed_ids,
        message=(
            f"检测到 {len(new_events)} 条新增、{len(updated_events)} 条更新、"
            f"{len(removed_ids)} 条从当前列表移除；需按重要性进一步分析。"
        ),
    )

def _event_semantics(event: OfficialEvent) -> tuple[str, str, str | None]:
    return (
        event.title,
        event.document_url,
        event.published_date.isoformat() if event.published_date else None,
    )


def _diff(
    current: SemanticEventSnapshot,
    *,
    status: EventDiffStatus,
    previous: SemanticEventSnapshot | None,
    message: str,
    is_baseline: bool = False,
    new_events: tuple[OfficialEvent, ...] = (),
    updated_events: tuple[OfficialEvent, ...] = (),
    removed_document_ids: tuple[str, ...] = (),
) -> EventSnapshotDiff:
    return EventSnapshotDiff(
        ticker=current.ticker,
        source_id=current.source_id,
        status=status,
        is_baseline=is_baseline,
        new_events=new_events,
        updated_events=updated_events,
        removed_document_ids=removed_document_ids,
        previous_hash=previous.semantic_hash if previous else None,
        current_hash=current.semantic_hash,
        message=message,
    )
