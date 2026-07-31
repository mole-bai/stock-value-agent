"""Parser for the Shanghai Stock Exchange company-bulletin JSON endpoint."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit

from .models import OfficialEvent, OfficialEventSource
from .parser import canonicalize_document_url, normalize_text


_SSE_DOCUMENT_BASE = "https://www.sse.com.cn/"


def parse_sse_announcement_json(
    body: bytes, *, source: OfficialEventSource
) -> tuple[OfficialEvent, ...]:
    """Normalize SSE bulletin records without treating malformed data as empty."""

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("SSE announcement response is not valid UTF-8 JSON") from exc
    page_help = document.get("pageHelp") if isinstance(document, dict) else None
    raw_items = page_help.get("data") if isinstance(page_help, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("SSE announcement response is missing pageHelp.data")

    events: dict[str, OfficialEvent] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        title = normalize_text(str(raw_item.get("TITLE") or ""))
        raw_url = str(raw_item.get("URL") or "").strip()
        if not title or not raw_url:
            continue
        document_url = canonicalize_document_url(
            urljoin(_SSE_DOCUMENT_BASE, raw_url), base_url=_SSE_DOCUMENT_BASE
        )
        if not document_url:
            continue
        path_stem = PurePosixPath(urlsplit(document_url).path).stem.strip()
        document_id = path_stem or hashlib.sha256(
            document_url.encode("utf-8")
        ).hexdigest()
        raw_date = str(raw_item.get("SSEDATE") or "").strip()
        try:
            published_date = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            published_date = None
        events[document_id] = OfficialEvent(
            ticker=source.ticker,
            source_id=source.source_id,
            document_id=document_id,
            title=title,
            document_url=document_url,
            published_date=published_date,
        )
    return tuple(
        sorted(
            events.values(),
            key=lambda item: (
                item.published_date or date.min,
                item.document_id,
            ),
            reverse=True,
        )
    )


__all__ = ["parse_sse_announcement_json"]
