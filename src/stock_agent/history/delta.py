"""Deterministic point-in-time run comparison and recommendation attribution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping


class ReasonCode(str, Enum):
    PRICE = "price"
    NEW_FUNDAMENTAL = "new_fundamental"
    NEW_EVENT = "new_event"
    ASSUMPTION = "assumption"
    RULE = "rule"
    DATA_QUALITY = "data_quality"


REASON_ORDER = {code: index for index, code in enumerate(ReasonCode)}


class ChangeKind(str, Enum):
    BASELINE = "baseline"
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class SignalTransitionKind(str, Enum):
    BASELINE = "baseline"
    ADDED = "added"
    UPGRADED = "upgraded"
    DOWNGRADED = "downgraded"
    RESOLVED = "resolved"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class ScalarChange:
    field: str
    kind: ChangeKind
    previous: Any
    current: Any
    absolute_change: Decimal | None = None
    percent_change: Decimal | None = None

    @property
    def changed(self) -> bool:
        return self.kind not in {ChangeKind.BASELINE, ChangeKind.UNCHANGED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "kind": self.kind.value,
            "previous": _json_safe(self.previous),
            "current": _json_safe(self.current),
            "absolute_change": _decimal_string(self.absolute_change),
            "percent_change": _decimal_string(self.percent_change),
        }


@dataclass(frozen=True, slots=True)
class EventChange:
    event_id: str
    kind: ChangeKind
    title: str
    published_at: str | None
    source_url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "title": self.title,
            "published_at": self.published_at,
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class SignalTransition:
    signal_id: str
    title: str
    transition: SignalTransitionKind
    previous_severity: str | None
    current_severity: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "title": self.title,
            "transition": self.transition.value,
            "previous_severity": self.previous_severity,
            "current_severity": self.current_severity,
        }


@dataclass(frozen=True, slots=True)
class RecommendationChange:
    action: ScalarChange
    confidence: ScalarChange
    valuation_center: ScalarChange
    reason_codes: tuple[ReasonCode, ...]

    @property
    def changed(self) -> bool:
        return any(
            item.changed for item in (self.action, self.confidence, self.valuation_center)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "action": self.action.to_dict(),
            "confidence": self.confidence.to_dict(),
            "valuation_center": self.valuation_center.to_dict(),
            "reason_codes": [code.value for code in self.reason_codes],
        }


@dataclass(frozen=True, slots=True)
class StockDelta:
    symbol: str
    status: ChangeKind
    price: ScalarChange
    metrics: tuple[ScalarChange, ...]
    events: tuple[EventChange, ...]
    signals: tuple[SignalTransition, ...]
    recommendation: RecommendationChange
    reason_codes: tuple[ReasonCode, ...]

    @property
    def has_changes(self) -> bool:
        if self.status is ChangeKind.BASELINE:
            return False
        if self.status in {ChangeKind.ADDED, ChangeKind.REMOVED, ChangeKind.CHANGED}:
            return True
        return bool(
            self.price.changed
            or self.metrics
            or self.events
            or self.signals
            or self.recommendation.changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "has_changes": self.has_changes,
            "price": self.price.to_dict(),
            "metrics": [change.to_dict() for change in self.metrics],
            "events": [change.to_dict() for change in self.events],
            "signals": [change.to_dict() for change in self.signals],
            "recommendation": self.recommendation.to_dict(),
            "reason_codes": [code.value for code in self.reason_codes],
        }


@dataclass(frozen=True, slots=True)
class RunDelta:
    previous_run_at: str | None
    current_run_at: str | None
    baseline: bool
    stocks: tuple[StockDelta, ...]

    @property
    def changed_stock_count(self) -> int:
        return sum(stock.has_changes for stock in self.stocks)

    def to_dict(self) -> dict[str, Any]:
        signal_counts = {kind.value: 0 for kind in SignalTransitionKind}
        for stock in self.stocks:
            for signal in stock.signals:
                signal_counts[signal.transition.value] += 1
        return {
            "schema_version": 1,
            "previous_run_at": self.previous_run_at,
            "current_run_at": self.current_run_at,
            "baseline": self.baseline,
            "summary": {
                "stock_count": len(self.stocks),
                "changed_stock_count": self.changed_stock_count,
                "price_changes": sum(stock.price.changed for stock in self.stocks),
                "metric_changes": sum(
                    sum(change.changed for change in stock.metrics) for stock in self.stocks
                ),
                "event_changes": sum(
                    sum(change.kind is not ChangeKind.BASELINE for change in stock.events)
                    for stock in self.stocks
                ),
                "signal_transitions": signal_counts,
                "recommendation_changes": sum(
                    stock.recommendation.changed for stock in self.stocks
                ),
            },
            "stocks": [stock.to_dict() for stock in self.stocks],
        }


def compare_runs(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> RunDelta:
    """Compare two run mappings without using future or external state."""

    if previous is not None and not isinstance(previous, Mapping):
        raise TypeError("previous run must be a mapping or None")
    if not isinstance(current, Mapping):
        raise TypeError("current run must be a mapping")
    baseline = previous is None or not previous
    previous_stocks = {} if baseline else _stocks_by_symbol(previous)
    current_stocks = _stocks_by_symbol(current)
    deltas = tuple(
        _compare_stock(
            symbol,
            previous_stocks.get(symbol),
            current_stocks.get(symbol),
            baseline=baseline,
        )
        for symbol in sorted(set(previous_stocks) | set(current_stocks))
    )
    return RunDelta(
        previous_run_at=(
            None if baseline else _optional_text(previous.get("run_at"))  # type: ignore[union-attr]
        ),
        current_run_at=_optional_text(current.get("run_at")),
        baseline=baseline,
        stocks=deltas,
    )


diff_runs = compare_runs


def _compare_stock(
    symbol: str,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    baseline: bool,
) -> StockDelta:
    previous = previous or {}
    current = current or {}
    if baseline:
        status = ChangeKind.BASELINE
    elif not previous:
        status = ChangeKind.ADDED
    elif not current:
        status = ChangeKind.REMOVED
    else:
        status = ChangeKind.UNCHANGED

    initial_kind = (
        ChangeKind.BASELINE
        if baseline
        else ChangeKind.ADDED
        if not previous
        else ChangeKind.REMOVED
        if not current
        else None
    )
    previous_price = _dig(previous, "price", "value")
    current_price = _dig(current, "price", "value")
    price = _scalar_change(
        "price", previous_price, current_price, forced_kind=initial_kind
    )

    previous_metrics = _metric_values(previous.get("metrics"))
    current_metrics = _metric_values(current.get("metrics"))
    metric_changes: list[ScalarChange] = []
    for name in sorted(set(previous_metrics) | set(current_metrics)):
        change = _scalar_change(
            name,
            previous_metrics.get(name),
            current_metrics.get(name),
            forced_kind=initial_kind,
        )
        if change.kind is not ChangeKind.UNCHANGED:
            metric_changes.append(change)

    event_changes = _compare_events(
        _items(previous.get("events")),
        _items(current.get("events")),
        initial_kind=initial_kind,
    )
    signal_changes = _compare_signals(
        _items(previous.get("signals")),
        _items(current.get("signals")),
        initial_kind=initial_kind,
    )

    previous_rec = _mapping(previous.get("recommendation"))
    current_rec = _mapping(current.get("recommendation"))
    previous_action = _recommendation_action(previous, previous_rec)
    current_action = _recommendation_action(current, current_rec)
    previous_confidence = _recommendation_confidence(previous, previous_rec)
    current_confidence = _recommendation_confidence(current, current_rec)
    valuation = _scalar_change(
        "valuation_center",
        _valuation_center(previous),
        _valuation_center(current),
        forced_kind=initial_kind,
    )

    reasons: set[ReasonCode] = set()
    if not baseline:
        if price.changed:
            reasons.add(ReasonCode.PRICE)
        if any(
            change.changed and not _price_derived_metric(change.field)
            for change in metric_changes
        ):
            reasons.add(ReasonCode.NEW_FUNDAMENTAL)
        if event_changes or signal_changes:
            reasons.add(ReasonCode.NEW_EVENT)
        if _assumption_fingerprint(previous) != _assumption_fingerprint(current):
            reasons.add(ReasonCode.ASSUMPTION)
        if _rule_fingerprint(previous) != _rule_fingerprint(current):
            reasons.add(ReasonCode.RULE)
        if _quality_fingerprint(previous) != _quality_fingerprint(current):
            reasons.add(ReasonCode.DATA_QUALITY)
        if not current:
            reasons.add(ReasonCode.DATA_QUALITY)

    action_change = _scalar_change(
        "action", previous_action, current_action, forced_kind=initial_kind
    )
    confidence_change = _scalar_change(
        "confidence", previous_confidence, current_confidence, forced_kind=initial_kind
    )
    recommendation_changed = any(
        item.changed for item in (action_change, confidence_change, valuation)
    )
    if recommendation_changed and not reasons and not baseline:
        reasons.add(ReasonCode.RULE)
    ordered_reasons = _ordered_reasons(reasons)
    recommendation = RecommendationChange(
        action=action_change,
        confidence=confidence_change,
        valuation_center=valuation,
        reason_codes=ordered_reasons if recommendation_changed else (),
    )

    if status is ChangeKind.UNCHANGED and (
        price.changed
        or metric_changes
        or event_changes
        or signal_changes
        or recommendation.changed
        or reasons
    ):
        status = ChangeKind.CHANGED
    return StockDelta(
        symbol=symbol,
        status=status,
        price=price,
        metrics=tuple(metric_changes),
        events=event_changes,
        signals=signal_changes,
        recommendation=recommendation,
        reason_codes=ordered_reasons,
    )


def _scalar_change(
    field: str,
    previous: Any,
    current: Any,
    *,
    forced_kind: ChangeKind | None = None,
) -> ScalarChange:
    if forced_kind is not None:
        kind = forced_kind
    elif previous is None and current is not None:
        kind = ChangeKind.ADDED
    elif previous is not None and current is None:
        kind = ChangeKind.REMOVED
    elif _equivalent(previous, current):
        kind = ChangeKind.UNCHANGED
    else:
        kind = ChangeKind.CHANGED
    old_number = _decimal(previous)
    new_number = _decimal(current)
    absolute = None
    percent = None
    if old_number is not None and new_number is not None and kind is ChangeKind.CHANGED:
        absolute = new_number - old_number
        if old_number != 0:
            percent = absolute / abs(old_number)
    return ScalarChange(
        field=field,
        kind=kind,
        previous=_json_safe(previous),
        current=_json_safe(current),
        absolute_change=absolute,
        percent_change=percent,
    )


def _compare_events(
    previous_items: tuple[Mapping[str, Any], ...],
    current_items: tuple[Mapping[str, Any], ...],
    *,
    initial_kind: ChangeKind | None,
) -> tuple[EventChange, ...]:
    previous = {_item_id(item, "event"): item for item in previous_items}
    current = {_item_id(item, "event"): item for item in current_items}
    changes: list[EventChange] = []
    for identifier in sorted(set(previous) | set(current)):
        old, new = previous.get(identifier), current.get(identifier)
        selected = new or old or {}
        if initial_kind is ChangeKind.BASELINE:
            kind = ChangeKind.BASELINE
        elif old is None:
            kind = ChangeKind.ADDED
        elif new is None:
            kind = ChangeKind.REMOVED
        elif _event_fingerprint(old) != _event_fingerprint(new):
            kind = ChangeKind.CHANGED
        else:
            continue
        changes.append(
            EventChange(
                event_id=identifier,
                kind=kind,
                title=str(selected.get("title", selected.get("name", identifier))),
                published_at=_optional_text(
                    selected.get("published_at", selected.get("event_time"))
                ),
                source_url=_optional_text(
                    selected.get("source_url", selected.get("url"))
                ),
            )
        )
    return tuple(changes)


def _compare_signals(
    previous_items: tuple[Mapping[str, Any], ...],
    current_items: tuple[Mapping[str, Any], ...],
    *,
    initial_kind: ChangeKind | None,
) -> tuple[SignalTransition, ...]:
    previous = {_item_id(item, "signal"): item for item in previous_items}
    current = {_item_id(item, "signal"): item for item in current_items}
    changes: list[SignalTransition] = []
    for identifier in sorted(set(previous) | set(current)):
        old, new = previous.get(identifier), current.get(identifier)
        selected = new or old or {}
        old_severity = _severity(old)
        new_severity = _severity(new)
        if initial_kind is ChangeKind.BASELINE:
            transition = SignalTransitionKind.BASELINE
        elif old is not None and _resolved(old) and new is None:
            continue
        elif old is None or _resolved(old):
            transition = (
                SignalTransitionKind.RESOLVED
                if new is not None and _resolved(new)
                else SignalTransitionKind.ADDED
            )
        elif new is None or _resolved(new):
            transition = SignalTransitionKind.RESOLVED
        else:
            old_rank, new_rank = _severity_rank(old_severity), _severity_rank(new_severity)
            if new_rank > old_rank:
                transition = SignalTransitionKind.UPGRADED
            elif new_rank < old_rank:
                transition = SignalTransitionKind.DOWNGRADED
            elif _signal_fingerprint(old) != _signal_fingerprint(new):
                transition = SignalTransitionKind.UPDATED
            else:
                continue
        changes.append(
            SignalTransition(
                signal_id=identifier,
                title=str(
                    selected.get(
                        "title",
                        selected.get("name", selected.get("description", identifier)),
                    )
                ),
                transition=transition,
                previous_severity=old_severity,
                current_severity=new_severity,
            )
        )
    return tuple(changes)


def _stocks_by_symbol(run: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = run.get("stocks", ())
    result: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw, Mapping):
        iterable: Iterable[tuple[Any, Any]] = raw.items()
    elif isinstance(raw, (list, tuple)):
        iterable = ((None, item) for item in raw)
    else:
        raise ValueError("run.stocks must be a mapping or sequence")
    for fallback, value in iterable:
        if not isinstance(value, Mapping):
            raise ValueError("every stock snapshot must be a mapping")
        symbol = str(
            value.get(
                "symbol",
                value.get("security_id", value.get("ticker", fallback or "")),
            )
        ).strip()
        if not symbol:
            raise ValueError("stock snapshot is missing symbol")
        if symbol in result:
            raise ValueError(f"duplicate stock snapshot: {symbol}")
        result[symbol] = value
    return result


def _metric_values(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    nested = raw.get("metrics")
    if isinstance(nested, Mapping):
        raw = nested
    result: dict[str, Any] = {}
    for key, item in raw.items():
        if isinstance(item, Mapping):
            result[str(key)] = item.get("value", item.get("current", item.get("amount")))
        else:
            result[str(key)] = item
    return result


def _valuation_center(stock: Mapping[str, Any]) -> Decimal | None:
    candidates = (
        _dig(stock, "recommendation", "valuation", "base", "value"),
        _dig(stock, "recommendation", "valuation", "base"),
        _dig(stock, "recommendation", "scenario_values", "base"),
        _dig(stock, "audit", "recommendation", "scenario_values", "base"),
        _dig(
            stock,
            "audit",
            "valuation",
            "scenarios",
            "base",
            "intrinsic_value_per_share",
        ),
    )
    for candidate in candidates:
        number = _decimal(candidate)
        if number is not None:
            return number
    low = _decimal(_dig(stock, "recommendation", "valuation", "bear", "value"))
    high = _decimal(_dig(stock, "recommendation", "valuation", "bull", "value"))
    if low is not None and high is not None:
        return (low + high) / Decimal("2")
    raw_range = _dig(stock, "audit", "valuation", "intrinsic_value_range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
        low, high = _decimal(raw_range[0]), _decimal(raw_range[1])
        if low is not None and high is not None:
            return (low + high) / Decimal("2")
    return None


def _recommendation_action(
    stock: Mapping[str, Any], recommendation: Mapping[str, Any]
) -> Any:
    return recommendation.get(
        "action_code",
        _dig(stock, "audit", "recommendation", "action")
        or recommendation.get("action"),
    )


def _recommendation_confidence(
    stock: Mapping[str, Any], recommendation: Mapping[str, Any]
) -> Any:
    return _dig(stock, "audit", "recommendation", "confidence") or recommendation.get(
        "confidence"
    )


def _assumption_fingerprint(stock: Mapping[str, Any]) -> str:
    display_valuation = _mapping(_dig(stock, "recommendation", "valuation"))
    display_assumptions = {
        name: _mapping(value).get("assumptions")
        for name, value in display_valuation.items()
        if isinstance(value, Mapping) and _mapping(value).get("assumptions") is not None
    }
    audit_scenarios = _mapping(_dig(stock, "audit", "valuation", "scenarios"))
    audit_assumptions = {
        name: _mapping(value).get("assumptions")
        for name, value in audit_scenarios.items()
        if isinstance(value, Mapping) and _mapping(value).get("assumptions") is not None
    }
    return _fingerprint(
        {
            "stock": stock.get("assumptions"),
            "recommendation": display_assumptions,
            "valuation_scenarios": audit_assumptions,
        },
        ignored={"evidence_ids"},
    )


def _rule_fingerprint(stock: Mapping[str, Any]) -> str:
    return _fingerprint(
        {
            "policy": _dig(stock, "audit", "recommendation", "policy"),
            "rule_trace": _dig(stock, "audit", "recommendation", "rule_trace"),
            "formula_audit": _dig(stock, "audit", "recommendation", "formula_audit"),
            "valuation_formula": _dig(stock, "audit", "valuation", "formula_version"),
            "rule_version": stock.get("rule_version"),
        },
        ignored={"actual", "evidence_ids", "explanation", "passed"},
    )


def _quality_fingerprint(stock: Mapping[str, Any]) -> str:
    return _fingerprint(
        {
            "price": {
                "freshness": _dig(stock, "price", "freshness"),
                "provisional": _dig(stock, "price", "provisional"),
                "provider": _dig(stock, "price", "provider"),
            },
            "data_quality": _dig(stock, "audit", "recommendation", "data_quality")
            or _dig(stock, "recommendation", "data_quality"),
            "data_gaps": _dig(stock, "recommendation", "data_gaps"),
        }
    )


def _event_fingerprint(item: Mapping[str, Any]) -> str:
    return _fingerprint(
        item,
        ignored={"observed_at", "fetched_at", "freshness", "provisional", "last_seen_at"},
    )


def _signal_fingerprint(item: Mapping[str, Any]) -> str:
    return _fingerprint(
        item,
        ignored={"observed_at", "fetched_at", "first_seen_at", "last_seen_at"},
    )


def _item_id(item: Mapping[str, Any], prefix: str) -> str:
    for key in (f"{prefix}_id", "id", "code", "rule_id", "evidence_id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    identity = {
        "title": item.get("title", item.get("name", item.get("description"))),
        "published_at": item.get("published_at", item.get("event_time")),
        "source_url": item.get("source_url", item.get("url")),
    }
    digest = hashlib.sha256(_fingerprint(identity).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _severity(item: Mapping[str, Any] | None) -> str | None:
    if item is None:
        return None
    value = item.get("severity", item.get("level"))
    return str(value).strip().lower() if value is not None else "information"


def _severity_rank(value: str | None) -> int:
    normalized = (value or "information").strip().lower()
    return {
        "information": 0,
        "info": 0,
        "positive": 0,
        "信息": 0,
        "yellow": 1,
        "watch": 1,
        "warning": 1,
        "黄色": 1,
        "orange": 2,
        "橙色": 2,
        "red": 3,
        "critical": 3,
        "红色": 3,
    }.get(normalized, 0)


def _resolved(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status", "active")).strip().lower()
    return status in {"resolved", "cleared", "inactive", "解除", "已解除"}


def _items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(item for item in value.values() if isinstance(item, Mapping))
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dig(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (Decimal, int, float, str)):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _equivalent(left: Any, right: Any) -> bool:
    left_number, right_number = _decimal(left), _decimal(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return _json_safe(left) == _json_safe(right)


def _decimal_string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _ordered_reasons(values: Iterable[ReasonCode]) -> tuple[ReasonCode, ...]:
    return tuple(sorted(set(values), key=REASON_ORDER.__getitem__))


def _price_derived_metric(name: str) -> bool:
    normalized = name.casefold()
    return any(
        marker in normalized
        for marker in (
            "price_to_",
            "market_cap",
            "enterprise_value",
            "ev_to_",
            "yield",
            "市盈率",
            "股息率",
            "盈利收益率",
        )
    )


def _fingerprint(value: Any, *, ignored: set[str] | None = None) -> str:
    ignored = ignored or set()

    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): clean(child)
                for key, child in sorted(item.items(), key=lambda pair: str(pair[0]))
                if str(key) not in ignored
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return _json_safe(item)

    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(child)
            for key, child in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
