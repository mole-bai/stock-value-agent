"""Semantic monitoring for official issuer announcements and IR indexes."""

from .catalog import OFFICIAL_EVENT_SOURCES, sources_for
from .diff import diff_event_snapshots
from .models import (
    PARSER_VERSION,
    EventDiffStatus,
    EventScanStatus,
    EventSnapshotDiff,
    OfficialEvent,
    OfficialEventSource,
    SemanticEventSnapshot,
    SourceKind,
)
from .parser import (
    canonicalize_document_url,
    compute_semantic_hash,
    extract_date,
    normalize_text,
    parse_official_event_html,
)
from .provider import OfficialEventSemanticProvider

__all__ = [
    "PARSER_VERSION",
    "OFFICIAL_EVENT_SOURCES",
    "EventDiffStatus",
    "EventScanStatus",
    "EventSnapshotDiff",
    "OfficialEvent",
    "OfficialEventSemanticProvider",
    "OfficialEventSource",
    "SemanticEventSnapshot",
    "SourceKind",
    "canonicalize_document_url",
    "compute_semantic_hash",
    "diff_event_snapshots",
    "extract_date",
    "normalize_text",
    "parse_official_event_html",
    "sources_for",
]
