"""Atomic local-file delivery for generated reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Audit-friendly result of a successful local delivery."""

    path: Path
    bytes_written: int
    sha256: str
    delivered_at: datetime


class LocalFileDelivery:
    """Write a report beneath one root directory using ``os.replace``.

    The temporary file is created in the destination directory, so replacing an
    existing report remains atomic on the destination filesystem.
    """

    def __init__(self, root: str | os.PathLike[str], *, encoding: str = "utf-8") -> None:
        self.root = Path(root)
        self.encoding = encoding

    def deliver(self, content: str, *, filename: str) -> DeliveryReceipt:
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        target = self._target(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = content.encode(self.encoding)
        temporary_path: Path | None = None

        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary_path = Path(raw_path)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
            self._sync_directory(target.parent)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

        return DeliveryReceipt(
            path=target,
            bytes_written=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            delivered_at=datetime.now(timezone.utc),
        )

    def _target(self, filename: str) -> Path:
        if not filename or filename in {".", ".."}:
            raise ValueError("filename must name a report file")
        candidate = Path(filename)
        if candidate.is_absolute() or len(candidate.parts) != 1:
            raise ValueError("filename must not contain a directory or path traversal")
        return self.root / candidate

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        """Persist the rename where the platform permits directory fsync."""

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
