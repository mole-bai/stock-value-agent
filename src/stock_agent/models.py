"""Small dependency-free domain models used by the orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Any, Mapping


class ModelError(ValueError):
    """Raised when a domain model cannot be constructed safely."""


def decimal(value: Any, *, field_name: str) -> Decimal:
    """Convert JSON-safe input to Decimal without passing through binary float."""

    if isinstance(value, bool) or value is None:
        raise ModelError(f"{field_name} must be numeric")
    try:
        return Decimal(str(value))
    except Exception as exc:  # Decimal exposes several concrete parse exceptions.
        raise ModelError(f"{field_name} must be numeric") from exc


@dataclass(frozen=True)
class OfficialSource:
    label: str
    url: str
    kind: str = "official"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OfficialSource":
        label = str(value.get("label", "")).strip()
        url = str(value.get("url", "")).strip()
        if not label or not url.startswith(("https://", "http://")):
            raise ModelError("official source requires label and http(s) url")
        return cls(label=label, url=url, kind=str(value.get("kind", "official")))


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    name: str
    market: str
    exchange: str
    currency: str
    timezone: str
    thesis: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    invalidation: tuple[str, ...] = ()
    sources: tuple[OfficialSource, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WatchItem":
        required = ("symbol", "name", "market", "exchange", "currency", "timezone")
        missing = [key for key in required if not str(value.get(key, "")).strip()]
        if missing:
            raise ModelError(f"watch item missing: {', '.join(missing)}")
        return cls(
            symbol=str(value["symbol"]).strip(),
            name=str(value["name"]).strip(),
            market=str(value["market"]).strip(),
            exchange=str(value["exchange"]).strip(),
            currency=str(value["currency"]).strip(),
            timezone=str(value["timezone"]).strip(),
            thesis=tuple(str(x).strip() for x in value.get("thesis", []) if str(x).strip()),
            risks=tuple(str(x).strip() for x in value.get("risks", []) if str(x).strip()),
            invalidation=tuple(
                str(x).strip() for x in value.get("invalidation", []) if str(x).strip()
            ),
            sources=tuple(
                OfficialSource.from_mapping(item) for item in value.get("sources", [])
            ),
        )


@dataclass(frozen=True)
class RecommendationPolicy:
    scope: str = "company_research"
    horizon_years: int = 5
    minimum_buy_return: Decimal = Decimal("0.12")
    minimum_hold_return: Decimal = Decimal("0.08")
    margin_of_safety: Decimal = Decimal("0.25")
    personalized: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecommendationPolicy":
        horizon = int(value.get("horizon_years", 5))
        if not 1 <= horizon <= 20:
            raise ModelError("horizon_years must be between 1 and 20")
        scope = str(value.get("scope", "company_research"))
        if scope not in {"company_research", "personalized"}:
            raise ModelError("recommendation scope must be company_research or personalized")
        personalized = bool(value.get("personalized", scope == "personalized"))
        return cls(
            scope=scope,
            horizon_years=horizon,
            minimum_buy_return=decimal(
                value.get("minimum_buy_return", "0.12"), field_name="minimum_buy_return"
            ),
            minimum_hold_return=decimal(
                value.get("minimum_hold_return", "0.08"), field_name="minimum_hold_return"
            ),
            margin_of_safety=decimal(
                value.get("margin_of_safety", "0.25"), field_name="margin_of_safety"
            ),
            personalized=personalized,
        )


def _clock(value: Any, *, field_name: str) -> time:
    text = str(value).strip()
    pieces = text.split(":")
    if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
        raise ModelError(f"{field_name} must use HH:MM")
    hour, minute = (int(piece) for piece in pieces)
    if hour not in range(24) or minute not in range(60):
        raise ModelError(f"{field_name} must use HH:MM")
    return time(hour, minute)


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = True
    quiet_start: time = time(22, 0)
    quiet_end: time = time(8, 0)
    bypass_through: str = "P0"
    write_markdown: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NotificationSettings":
        bypass = str(value.get("bypass_through", "P0")).upper()
        if bypass not in {"P0", "P1", "P2", "P3"}:
            raise ModelError("notification bypass_through must be P0, P1, P2 or P3")
        return cls(
            enabled=bool(value.get("enabled", True)),
            quiet_start=_clock(
                value.get("quiet_start", "22:00"), field_name="quiet_start"
            ),
            quiet_end=_clock(
                value.get("quiet_end", "08:00"), field_name="quiet_end"
            ),
            bypass_through=bypass,
            write_markdown=bool(value.get("write_markdown", True)),
        )


@dataclass(frozen=True)
class AgentSettings:
    mode: str
    timezone: str
    report_time: str
    output_dir: str
    state_file: str
    policy: RecommendationPolicy
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    watchlist: tuple[WatchItem, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentSettings":
        mode = str(value.get("mode", "personal_research"))
        if mode != "personal_research":
            raise ModelError("MVP only enables personal_research mode")
        watchlist = tuple(WatchItem.from_mapping(item) for item in value.get("watchlist", []))
        if not watchlist:
            raise ModelError("watchlist cannot be empty")
        symbols = [item.symbol for item in watchlist]
        if len(set(symbols)) != len(symbols):
            raise ModelError("watchlist symbols must be unique")
        report_time = str(value.get("report_time", "18:30"))
        pieces = report_time.split(":")
        if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
            raise ModelError("report_time must use HH:MM")
        hour, minute = (int(piece) for piece in pieces)
        if hour not in range(24) or minute not in range(60):
            raise ModelError("report_time must use HH:MM")
        return cls(
            mode=mode,
            timezone=str(value.get("timezone", "Asia/Shanghai")),
            report_time=report_time,
            output_dir=str(value.get("output_dir", "reports")),
            state_file=str(value.get("state_file", "var/state.json")),
            policy=RecommendationPolicy.from_mapping(value.get("recommendation_policy", {})),
            notifications=NotificationSettings.from_mapping(
                value.get("notifications", {})
            ),
            watchlist=watchlist,
        )
