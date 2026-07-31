"""Reusable HTTP transports for connector implementations."""

from __future__ import annotations

import re
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .base import ConnectorTransportError


@dataclass(frozen=True, slots=True)
class HttpResponse:
    body: bytes
    headers: Mapping[str, str]
    status: int = 200
    final_url: str | None = None


Transport = Callable[[str, Mapping[str, str], float], bytes | HttpResponse]
Runner = Callable[..., Any]


def response_body(response: bytes | HttpResponse) -> bytes:
    return response.body if isinstance(response, HttpResponse) else response


def response_url(response: bytes | HttpResponse, requested_url: str) -> str:
    if isinstance(response, HttpResponse) and response.final_url:
        return response.final_url
    return requested_url


class CurlTransport:
    """Safe curl-backed transport for hosts whose Python CA store is unusable.

    TLS verification remains enabled: this class intentionally never passes
    ``-k``/``--insecure``.  curl runs without a shell, follows only HTTP(S)
    redirects, and enforces both a wall-clock timeout and a response-size cap.
    """

    def __init__(
        self,
        *,
        executable: str = "curl",
        max_bytes: int = 5 * 1024 * 1024,
        max_header_bytes: int = 256 * 1024,
        runner: Runner = subprocess.run,
    ) -> None:
        if not executable.strip():
            raise ValueError("executable must not be empty")
        if max_bytes <= 0 or max_header_bytes <= 0:
            raise ValueError("response limits must be positive")
        self._executable = executable
        self._max_bytes = max_bytes
        self._max_header_bytes = max_header_bytes
        self._runner = runner

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("curl transport only accepts absolute HTTP(S) URLs")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        header_args: list[str] = []
        for name, value in headers.items():
            text = f"{name}: {value}"
            if not name or any(character in text for character in "\r\n\0"):
                raise ValueError("HTTP headers must not contain control characters")
            header_args.extend(("--header", text))

        with tempfile.TemporaryDirectory(prefix="stock-agent-curl-") as directory:
            body_path = Path(directory) / "body"
            header_path = Path(directory) / "headers"
            command = [
                self._executable,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--proto",
                "=http,https",
                "--proto-redir",
                "=http,https",
                "--max-time",
                str(timeout),
                "--max-filesize",
                str(self._max_bytes),
                "--output",
                str(body_path),
                "--dump-header",
                str(header_path),
                "--write-out",
                "%{http_code}\n%{url_effective}",
                *header_args,
                "--",
                url,
            ]
            try:
                completed = self._runner(
                    command,
                    shell=False,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout + 2,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ConnectorTransportError(f"curl request failed: {exc}") from exc
            if completed.returncode != 0:
                detail = bytes(completed.stderr or b"").decode(
                    "utf-8", errors="replace"
                )[:500]
                raise ConnectorTransportError(
                    f"curl request failed with exit {completed.returncode}: {detail}"
                )
            try:
                body_size = body_path.stat().st_size
                header_size = header_path.stat().st_size
            except OSError as exc:
                raise ConnectorTransportError("curl did not create response files") from exc
            if body_size > self._max_bytes:
                raise ConnectorTransportError(
                    f"curl response exceeded {self._max_bytes} bytes"
                )
            if header_size > self._max_header_bytes:
                raise ConnectorTransportError("curl response headers were too large")
            body = body_path.read_bytes()
            raw_headers = header_path.read_bytes()

        metadata = bytes(completed.stdout or b"").decode("utf-8", errors="replace")
        status_text, separator, final_url = metadata.partition("\n")
        if not separator or not status_text.isdigit():
            raise ConnectorTransportError("curl returned malformed response metadata")
        status = int(status_text)
        return HttpResponse(
            body=body,
            headers=_parse_last_header_block(raw_headers),
            status=status,
            final_url=final_url.strip() or url,
        )


def _parse_last_header_block(raw: bytes) -> dict[str, str]:
    blocks = re.split(br"\r?\n\r?\n", raw.strip())
    for block in reversed(blocks):
        lines = re.split(br"\r?\n", block)
        if not lines or not lines[0].startswith(b"HTTP/"):
            continue
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if separator:
                headers[name.decode("latin-1").strip()] = value.decode(
                    "latin-1"
                ).strip()
        return headers
    return {}
