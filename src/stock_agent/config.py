"""Configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AgentSettings, ModelError


class ConfigError(RuntimeError):
    """Raised when a config file is missing, malformed, or unsafe."""


def load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc
    try:
        value = json.loads(raw, parse_float=str, parse_int=str)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{target} must contain a JSON object")
    return value


def load_settings(path: str | Path) -> AgentSettings:
    try:
        return AgentSettings.from_mapping(load_json(path))
    except (ModelError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid settings in {path}: {exc}") from exc


def load_fundamentals(path: str | Path) -> dict[str, Any]:
    data = load_json(path)
    stocks = data.get("stocks")
    if not isinstance(stocks, dict) or not stocks:
        raise ConfigError("fundamentals file requires non-empty 'stocks' mapping")
    return data
