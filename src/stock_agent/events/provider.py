"""Fetch and semantically scan official announcement and IR sources."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .catalog import OFFICIAL_EVENT_SOURCES, sources_for
from .models import (
    EventScanStatus,
    OfficialEventSource,
    SemanticEventSnapshot,
    SourceKind,
    ensure_utc,
)
from .parser import compute_semantic_hash, parse_official_event_html
from .sse import parse_sse_announcement_json


Transport = Callable[[str, Mapping[str, str], float], Any]


def _default_transport(
    url: str, headers: Mapping[str, str], timeout: float
) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _SimpleResponse(
                body=response.read(),
                headers=dict(response.headers.items()),
                status=int(response.status),
                final_url=response.geturl(),
            )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"official event request failed: {exc}") from exc


class _SimpleResponse:
    def __init__(
        self,
        *,
        body: bytes,
        headers: Mapping[str, str],
        status: int,
        final_url: str,
    ) -> None:
        self.body = body
        self.headers = headers
        self.status = status
        self.final_url = final_url


class OfficialEventSemanticProvider:
    """Scan every configured official source for one watchlist security.

    The ``transport`` signature matches the connector-layer transports, so the
    existing TLS-verifying ``CurlTransport`` can be injected without an adapter.
    """

    provider_name = "official_event_semantics"

    def __init__(
        self,
        sources: Mapping[str, tuple[OfficialEventSource, ...]] | None = None,
        *,
        transport: Transport | None = None,
        timeout: float = 15.0,
        max_bytes: int = 5 * 1024 * 1024,
        user_agent: str = "stock-agent-personal-research/0.2",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._sources = {
            ticker: tuple(items)
            for ticker, items in (sources or OFFICIAL_EVENT_SOURCES).items()
        }
        self._transport = transport or _default_transport
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._user_agent = user_agent

    def scan_security(
        self, security: Any, *, now: datetime | None = None
    ) -> tuple[SemanticEventSnapshot, ...]:
        ticker = str(getattr(security, "ticker", ""))
        configured = tuple(self._sources.get(ticker, ()))
        if not configured:
            raise ValueError(f"no official event sources configured for {ticker or 'security'}")
        checked_at = ensure_utc(
            now or datetime.now(timezone.utc), field_name="now"
        )
        return tuple(
            self.scan_source(source, now=checked_at) for source in configured
        )

    def scan_source(
        self,
        source: OfficialEventSource,
        *,
        now: datetime | None = None,
    ) -> SemanticEventSnapshot:
        checked_at = ensure_utc(
            now or datetime.now(timezone.utc), field_name="now"
        )
        headers = {
            "Accept": (
                "application/json,*/*;q=0.5"
                if source.kind is SourceKind.STRUCTURED_API
                else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
            ),
            "User-Agent": self._user_agent,
        }
        if source.kind is SourceKind.STRUCTURED_API:
            headers["Referer"] = "https://www.sse.com.cn/"
        try:
            response = self._transport(source.url, headers, self._timeout)
            body, response_headers, status, final_url = _response_parts(
                response, requested_url=source.url
            )
            if not 200 <= status < 300:
                return self._degraded(
                    source,
                    checked_at,
                    source_url=final_url,
                    message=(
                        f"官方页面返回 HTTP {status}；无法判断是否有新增公告。"
                    ),
                )
            if len(body) > self._max_bytes:
                return self._degraded(
                    source,
                    checked_at,
                    source_url=final_url,
                    message="官方页面超过大小上限；无法判断是否有新增公告。",
                )
            if source.kind is SourceKind.STRUCTURED_API:
                events = parse_sse_announcement_json(body, source=source)
            else:
                html = _decode_html(body, response_headers)
                events = parse_official_event_html(
                    html,
                    source=source,
                    base_url=final_url,
                )
        except Exception as exc:  # transport/parser boundary: degrade, never infer empty
            return self._degraded(
                source,
                checked_at,
                source_url=source.url,
                message=(
                    "官方页面抓取或解析失败；无法判断是否有新增公告。"
                    f"错误类型：{type(exc).__name__}。"
                ),
            )

        if not events:
            status_value = (
                EventScanStatus.PORTAL_ONLY
                if source.kind is SourceKind.EXCHANGE_PORTAL
                else EventScanStatus.DEGRADED
            )
            return SemanticEventSnapshot(
                ticker=source.ticker,
                source_id=source.source_id,
                source_label=source.label,
                source_url=final_url,
                status=status_value,
                events=(),
                semantic_hash=None,
                observed_at=checked_at,
                fetched_at=checked_at,
                message=(
                    "未从官方来源提取到可核验的公告列表；页面可能依赖 JavaScript/API。"
                    "空提取结果不表示没有公告。"
                ),
                provisional=True,
                coverage_complete=False,
            )
        return SemanticEventSnapshot(
            ticker=source.ticker,
            source_id=source.source_id,
            source_label=source.label,
            source_url=final_url,
            status=EventScanStatus.EXTRACTED,
            events=events,
            semantic_hash=compute_semantic_hash(events),
            observed_at=checked_at,
            fetched_at=checked_at,
            message=(
                f"从官方来源提取 {len(events)} 条可识别记录；"
                "语义监控不承诺覆盖该发行人的全部公告。"
            ),
            provisional=True,
            coverage_complete=False,
        )

    @staticmethod
    def _degraded(
        source: OfficialEventSource,
        checked_at: datetime,
        *,
        source_url: str,
        message: str,
    ) -> SemanticEventSnapshot:
        return SemanticEventSnapshot(
            ticker=source.ticker,
            source_id=source.source_id,
            source_label=source.label,
            source_url=source_url,
            status=EventScanStatus.DEGRADED,
            events=(),
            semantic_hash=None,
            observed_at=checked_at,
            fetched_at=checked_at,
            message=message,
            provisional=True,
            coverage_complete=False,
        )


def _response_parts(
    response: Any, *, requested_url: str
) -> tuple[bytes, Mapping[str, str], int, str]:
    if isinstance(response, bytes):
        return response, {}, 200, requested_url
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        raise TypeError("transport response body must be bytes")
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        raise TypeError("transport response headers must be a mapping")
    status = int(getattr(response, "status", 200))
    final_url = str(getattr(response, "final_url", None) or requested_url)
    return body, headers, status, final_url


def _decode_html(body: bytes, headers: Mapping[str, str]) -> str:
    content_type = next(
        (
            str(value)
            for name, value in headers.items()
            if str(name).casefold() == "content-type"
        ),
        "",
    )
    header_charset = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    prefix = body[:4096].decode("ascii", errors="ignore")
    meta_charset = re.search(
        r"charset\s*=\s*['\"]?([\w.-]+)", prefix, flags=re.IGNORECASE
    )
    encodings = [
        match.group(1) for match in (header_charset, meta_charset) if match is not None
    ]
    encodings.extend(("utf-8", "gb18030"))
    for encoding in dict.fromkeys(encodings):
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


__all__ = ["OfficialEventSemanticProvider", "Transport", "sources_for"]
