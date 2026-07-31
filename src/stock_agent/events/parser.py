"""Conservative HTML-to-event normalization using only the standard library."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Iterable, Mapping

from .models import OfficialEvent, OfficialEventSource


_BLOCK_TAGS = {"article", "dd", "div", "li", "p", "section", "td", "tr"}
_SUPPRESSED_TAGS = {"script", "style", "svg", "template"}
_GENERIC_TITLES = {
    "download",
    "download pdf",
    "details",
    "more",
    "pdf",
    "read more",
    "view",
    "查看",
    "更多",
    "下载",
    "附件",
}
_EVENT_KEYWORDS = (
    "announcement",
    "annual report",
    "interim report",
    "quarterly report",
    "financial report",
    "financial results",
    "results announcement",
    "dividend",
    "earnings",
    "公告",
    "年度报告",
    "年报",
    "中期报告",
    "半年报",
    "季度报告",
    "季报",
    "财务报告",
    "业绩",
    "分红",
    "派息",
)
_DOCUMENT_EXTENSIONS = {
    ".doc",
    ".docx",
    ".pdf",
    ".xls",
    ".xlsx",
    ".zip",
}
_VOLATILE_QUERY_KEYS = {
    "_",
    "auth",
    "cache",
    "cachebuster",
    "cb",
    "csrf",
    "expires",
    "nonce",
    "random",
    "rnd",
    "session",
    "sid",
    "sign",
    "signature",
    "timestamp",
    "token",
    "ts",
    "v",
}
_STABLE_QUERY_KEYS = {
    "announcementid",
    "articleid",
    "bulletinid",
    "document",
    "documentid",
    "docid",
    "file",
    "fileid",
    "id",
    "newsid",
    "noticeid",
    "productid",
    "reportid",
    "stockid",
}
_GENERIC_PATH_NAMES = {
    "detail",
    "download",
    "file",
    "index",
    "index.html",
    "index.shtml",
    "view",
}


@dataclass(slots=True)
class _Anchor:
    href: str
    attrs: dict[str, str]
    text_parts: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Frame:
    tag: str
    text_parts: list[str] = field(default_factory=list)
    anchors: list[_Anchor] = field(default_factory=list)


class _EventHtmlCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._frames: list[_Frame] = []
        self._anchor: _Anchor | None = None
        self._suppressed_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _SUPPRESSED_TAGS:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if normalized_tag in _BLOCK_TAGS:
            self._frames.append(_Frame(normalized_tag))
        if normalized_tag == "a" and self._anchor is None:
            mapping = {
                str(name).casefold(): str(value)
                for name, value in attrs
                if value is not None
            }
            href = mapping.get("href", "").strip()
            if href:
                self._anchor = _Anchor(href=href, attrs=mapping)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth or not data:
            return
        if self._anchor is not None:
            self._anchor.text_parts.append(data)
        for frame in self._frames:
            frame.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _SUPPRESSED_TAGS:
            if self._suppressed_depth:
                self._suppressed_depth -= 1
            return
        if self._suppressed_depth:
            return
        if normalized_tag == "a" and self._anchor is not None:
            anchor = self._anchor
            self._anchor = None
            self.anchors.append(anchor)
            for frame in self._frames:
                frame.anchors.append(anchor)
        if normalized_tag in _BLOCK_TAGS:
            matching_index = next(
                (
                    index
                    for index in range(len(self._frames) - 1, -1, -1)
                    if self._frames[index].tag == normalized_tag
                ),
                None,
            )
            if matching_index is not None:
                while len(self._frames) > matching_index:
                    self._finish_frame(self._frames.pop())

    def close(self) -> None:
        super().close()
        if self._anchor is not None:
            self.anchors.append(self._anchor)
            for frame in self._frames:
                frame.anchors.append(self._anchor)
            self._anchor = None
        while self._frames:
            self._finish_frame(self._frames.pop())

    @staticmethod
    def _finish_frame(frame: _Frame) -> None:
        context = normalize_text(" ".join(frame.text_parts))
        if not context or len(context) > 2_000:
            return
        for anchor in frame.anchors:
            if context not in anchor.contexts:
                anchor.contexts.append(context)


def normalize_text(value: str) -> str:
    """Normalize display text while retaining meaningful punctuation."""

    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_document_url(raw_url: str, *, base_url: str) -> str | None:
    """Resolve a document URL and remove known volatile query material."""

    absolute = urllib.parse.urljoin(base_url, raw_url.strip())
    parsed = urllib.parse.urlsplit(absolute)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold()
    try:
        port = parsed.port
    except ValueError:
        return None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    decoded_path = urllib.parse.unquote(parsed.path or "/")
    normalized_path = posixpath.normpath(decoded_path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    path = urllib.parse.quote(
        normalized_path,
        safe="/!$&'()*+,-.:;=@_~",
    )
    pairs: list[tuple[str, str]] = []
    for raw_name, raw_value in urllib.parse.parse_qsl(
        parsed.query, keep_blank_values=True
    ):
        name = normalize_text(raw_name).casefold()
        if (
            not name
            or name.startswith("utm_")
            or name in _VOLATILE_QUERY_KEYS
            or name not in _STABLE_QUERY_KEYS
        ):
            continue
        pairs.append((name, normalize_text(raw_value)))
    query = urllib.parse.urlencode(sorted(set(pairs)))
    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def parse_official_event_html(
    html: str,
    *,
    source: OfficialEventSource,
    base_url: str | None = None,
) -> tuple[OfficialEvent, ...]:
    """Extract conservative event records from one official HTML document."""

    collector = _EventHtmlCollector()
    collector.feed(html)
    collector.close()
    candidates: list[OfficialEvent] = []
    effective_base = base_url or source.url

    for anchor in collector.anchors:
        document_url = canonicalize_document_url(anchor.href, base_url=effective_base)
        if document_url is None:
            continue
        context = _best_context(anchor)
        published_date = extract_date(
            " ".join(
                part
                for part in (
                    context,
                    anchor.attrs.get("data-date"),
                    anchor.attrs.get("datetime"),
                    anchor.href,
                )
                if part
            )
        )
        title = _select_title(anchor, context=context, published_date=published_date)
        if title is None or not _looks_like_event(
            document_url,
            title=title,
            published_date=published_date,
            attrs=anchor.attrs,
        ):
            continue
        document_id = _document_id(
            document_url,
            title=title,
            attrs=anchor.attrs,
        )
        candidates.append(
            OfficialEvent(
                ticker=source.ticker,
                source_id=source.source_id,
                document_id=document_id,
                title=title,
                document_url=document_url,
                published_date=published_date,
            )
        )

    return _deduplicate(candidates)


def compute_semantic_hash(events: Iterable[OfficialEvent]) -> str:
    """Hash only normalized event semantics, independent of page chrome."""

    records = [
        {
            "document_id": event.document_id,
            "title": normalize_text(event.title),
            "published_date": (
                event.published_date.isoformat() if event.published_date else None
            ),
            "document_url": event.document_url,
        }
        for event in events
    ]
    records.sort(
        key=lambda item: (
            str(item["document_id"]),
            str(item["published_date"] or ""),
            str(item["title"]),
        )
    )
    payload = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def extract_date(value: str) -> date | None:
    """Recognize common Chinese, ISO, slash and English announcement dates."""

    text = normalize_text(value)
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])[-/.月](0?[1-9]|[12]\d|3[01])日?(?!\d)",
            ("%Y", "%m", "%d"),
        ),
        (
            r"(?<!\d)(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})(?!\d)",
            ("%d", "%m", "%Y"),
        ),
    )
    for pattern, order in patterns:
        match = re.search(pattern, text)
        if match:
            values = dict(zip(order, (int(group) for group in match.groups())))
            try:
                return date(values["%Y"], values["%m"], values["%d"])
            except ValueError:
                continue
    eight_digits = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-3]\d)(?!\d)", text)
    if eight_digits:
        try:
            return date(*(int(group) for group in eight_digits.groups()))
        except ValueError:
            pass
    for pattern in (r"\b\d{1,2}\s+[A-Za-z]+\s+20\d{2}\b", r"\b[A-Za-z]+\s+\d{1,2},?\s+20\d{2}\b"):
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = match.group(0).replace(",", "")
        for format_string in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(candidate, format_string).date()
            except ValueError:
                continue
    return None


def _best_context(anchor: _Anchor) -> str:
    if not anchor.contexts:
        return normalize_text(" ".join(anchor.text_parts))
    own = normalize_text(" ".join(anchor.text_parts))
    candidates = [context for context in anchor.contexts if len(context) <= 600]
    with_dates = [context for context in candidates if extract_date(context) is not None]
    pool = with_dates or candidates
    if not pool:
        return own
    return min(pool, key=lambda value: (len(value), value))


def _select_title(
    anchor: _Anchor, *, context: str, published_date: date | None
) -> str | None:
    candidates = [
        anchor.attrs.get("data-title", ""),
        anchor.attrs.get("aria-label", ""),
        anchor.attrs.get("title", ""),
        " ".join(anchor.text_parts),
    ]
    normalized = [normalize_text(candidate) for candidate in candidates if candidate]
    for candidate in normalized:
        if _is_substantive_title(candidate):
            return candidate[:300]
    context_title = context
    if published_date is not None:
        context_title = _remove_date_fragments(context_title)
    context_title = re.sub(
        r"\b(?:download|view|read more|pdf)\b|(?:下载|查看|更多|附件)",
        " ",
        context_title,
        flags=re.IGNORECASE,
    )
    context_title = normalize_text(context_title).strip("-|–—:：|· ")
    return context_title[:300] if _is_substantive_title(context_title) else None


def _is_substantive_title(value: str) -> bool:
    normalized = normalize_text(value)
    return (
        4 <= len(normalized) <= 300
        and normalized.casefold().strip(".:-_ ") not in _GENERIC_TITLES
        and any(character.isalpha() or "\u3400" <= character <= "\u9fff" for character in normalized)
    )


def _looks_like_event(
    document_url: str,
    *,
    title: str,
    published_date: date | None,
    attrs: Mapping[str, str],
) -> bool:
    path = urllib.parse.unquote(urllib.parse.urlsplit(document_url).path).casefold()
    extension = posixpath.splitext(path)[1]
    if extension in _DOCUMENT_EXTENSIONS:
        return True
    if any(
        key in attrs
        for key in ("data-doc-id", "data-document-id", "data-announcement-id", "data-id")
    ):
        return True
    query_names = {
        name.casefold()
        for name, _value in urllib.parse.parse_qsl(
            urllib.parse.urlsplit(document_url).query, keep_blank_values=True
        )
    }
    if query_names & _STABLE_QUERY_KEYS:
        return True
    folded_title = title.casefold()
    return published_date is not None and any(
        keyword in folded_title for keyword in _EVENT_KEYWORDS
    )


def _document_id(
    document_url: str, *, title: str, attrs: Mapping[str, str]
) -> str:
    for name in ("data-document-id", "data-doc-id", "data-announcement-id", "data-id"):
        value = normalize_text(attrs.get(name, ""))
        if value:
            return f"attr:{_safe_id(value)}"
    parsed = urllib.parse.urlsplit(document_url)
    for name, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if name.casefold() in _STABLE_QUERY_KEYS and value:
            return f"query:{name.casefold()}:{_safe_id(value)}"
    basename = urllib.parse.unquote(posixpath.basename(parsed.path)).casefold()
    stem = posixpath.splitext(basename)[0]
    if len(stem) >= 4 and basename not in _GENERIC_PATH_NAMES and stem not in _GENERIC_PATH_NAMES:
        return f"path:{_safe_id(stem)}"
    digest = hashlib.sha256(
        f"{document_url}|{normalize_text(title)}".encode("utf-8")
    ).hexdigest()[:24]
    return f"semantic:{digest}"


def _safe_id(value: str) -> str:
    normalized = normalize_text(value)
    safe = re.sub(r"[^0-9A-Za-z._:-]+", "-", normalized).strip("-")
    return safe[:160] if safe else hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _deduplicate(events: Iterable[OfficialEvent]) -> tuple[OfficialEvent, ...]:
    selected: dict[str, OfficialEvent] = {}
    for event in events:
        current = selected.get(event.document_id)
        if current is None or _event_quality(event) > _event_quality(current):
            selected[event.document_id] = event
    return tuple(
        sorted(
            selected.values(),
            key=lambda event: (
                event.published_date or date.min,
                event.document_id,
            ),
            reverse=True,
        )
    )


def _event_quality(event: OfficialEvent) -> tuple[int, int, str]:
    return (
        1 if event.published_date is not None else 0,
        len(event.title),
        event.title,
    )


def _remove_date_fragments(value: str) -> str:
    text = re.sub(
        r"(?<!\d)20\d{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12]\d|3[01])日?(?!\d)",
        " ",
        value,
    )
    text = re.sub(
        r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.]20\d{2}(?!\d)",
        " ",
        text,
    )
    return text
