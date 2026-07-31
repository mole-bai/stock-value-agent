"""Atomic local state store for point-in-time snapshots and recommendation history."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    """Raised when local state cannot be read or persisted."""


class JsonStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": 1,
                "last_run_at": None,
                "quotes": {},
                "source_hashes": {},
                "recommendations": {},
                "runs": [],
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot load state {self.path}: {exc}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise StateError(f"unsupported state schema in {self.path}")
        return value

    def save(self, value: dict[str, Any]) -> None:
        snapshot = deepcopy(value)
        snapshot["schema_version"] = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise StateError(f"cannot save state {self.path}: {exc}") from exc
