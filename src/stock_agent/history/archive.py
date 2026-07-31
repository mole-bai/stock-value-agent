"""Atomic local archive for immutable daily run snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SnapshotArchiveError(RuntimeError):
    pass


class InvalidSnapshotId(SnapshotArchiveError, ValueError):
    pass


class SnapshotNotFound(SnapshotArchiveError, FileNotFoundError):
    pass


class SnapshotArchive:
    """Save history first, then atomically advance ``latest.json``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.history_dir = self.root / "history"

    def save(
        self, snapshot: Mapping[str, Any], *, snapshot_id: str | None = None
    ) -> str:
        if not isinstance(snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")
        document = _normalize(snapshot)
        payload = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        identifier = snapshot_id or _default_snapshot_id(document, payload)
        identifier = self._validate_id(identifier)
        self._ensure_directories()
        history_path = self._history_path(identifier)
        if history_path.exists():
            if history_path.is_symlink():
                raise SnapshotArchiveError("history snapshot must not be a symbolic link")
            if history_path.read_bytes() != payload:
                raise SnapshotArchiveError(
                    f"snapshot {identifier!r} already exists with different content"
                )
        else:
            _atomic_write(history_path, payload)
        _atomic_write(self._latest_path(), payload)
        return identifier

    def load_latest(self) -> dict[str, Any] | None:
        self._ensure_directories()
        path = self._latest_path()
        if not path.exists():
            return None
        return self._read(path)

    def load(self, snapshot_id: str) -> dict[str, Any]:
        self._ensure_directories()
        path = self._history_path(self._validate_id(snapshot_id))
        if not path.exists():
            raise SnapshotNotFound(f"snapshot not found: {snapshot_id}")
        return self._read(path)

    def list_snapshot_ids(self, *, newest_first: bool = False) -> tuple[str, ...]:
        self._ensure_directories()
        identifiers: list[str] = []
        for path in self.history_dir.iterdir():
            if path.is_symlink():
                raise SnapshotArchiveError("history directory contains a symbolic link")
            if path.is_file() and path.suffix == ".json":
                identifiers.append(path.stem)
        return tuple(sorted(identifiers, reverse=newest_first))

    def load_history(
        self, *, limit: int | None = None, newest_first: bool = False
    ) -> tuple[dict[str, Any], ...]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        identifiers = self.list_snapshot_ids(newest_first=newest_first)
        if limit is not None:
            identifiers = identifiers[:limit]
        return tuple(self.load(identifier) for identifier in identifiers)

    def _validate_id(self, value: str) -> str:
        identifier = str(value)
        if (
            not _SAFE_ID.fullmatch(identifier)
            or identifier in {".", "..", "latest"}
            or ".." in identifier
        ):
            raise InvalidSnapshotId(f"unsafe snapshot id: {identifier!r}")
        return identifier

    def _ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise SnapshotArchiveError("archive root must not be a symbolic link")
        self.history_dir.mkdir(parents=True, exist_ok=True)
        resolved_history = self.history_dir.resolve()
        try:
            resolved_history.relative_to(self.root)
        except ValueError as exc:
            raise SnapshotArchiveError("history directory escapes archive root") from exc
        if self.history_dir.is_symlink():
            raise SnapshotArchiveError("history directory must not be a symbolic link")

    def _history_path(self, snapshot_id: str) -> Path:
        return self._safe_path(self.history_dir / f"{snapshot_id}.json")

    def _latest_path(self) -> Path:
        return self._safe_path(self.root / "latest.json")

    def _safe_path(self, path: Path) -> Path:
        if path.is_symlink():
            raise SnapshotArchiveError("archive files must not be symbolic links")
        resolved_parent = path.parent.resolve()
        try:
            resolved_parent.relative_to(self.root)
        except ValueError as exc:
            raise SnapshotArchiveError("archive path escapes root") from exc
        return path

    def _read(self, path: Path) -> dict[str, Any]:
        path = self._safe_path(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotArchiveError(f"cannot read snapshot {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise SnapshotArchiveError(f"snapshot {path.name} is not a JSON object")
        return value


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except OSError as exc:
        raise SnapshotArchiveError(f"cannot atomically save {path.name}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _default_snapshot_id(document: Mapping[str, Any], payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()[:12]
    raw_run_at = document.get("run_at")
    if raw_run_at is not None:
        try:
            parsed = datetime.fromisoformat(str(raw_run_at).replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                stamp = parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                return f"{stamp}-{digest}"
        except ValueError:
            pass
    return f"snapshot-{digest}"


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("snapshot Decimal values must be finite")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot datetimes must be timezone-aware")
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("snapshot mapping keys must be strings")
            result[key] = _normalize(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("snapshot float values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _normalize(to_dict())
    raise TypeError(f"snapshot value is not JSON serializable: {type(value).__name__}")
