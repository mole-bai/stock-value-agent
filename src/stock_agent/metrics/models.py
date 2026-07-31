"""Schema-friendly models used by the deterministic metric engine.

The project deliberately keeps these models dependency free.  They can be
constructed directly or from JSON-like dictionaries and always serialise
``Decimal`` values as strings so a report can be reproduced without binary
floating point drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


DecimalLike = Decimal | int | float | str


def to_decimal(value: DecimalLike | None, *, field_name: str = "value") -> Decimal | None:
    """Convert a schema value to ``Decimal`` without accepting booleans/NaN."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric, not boolean")
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not converted.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal")
    return converted


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


class MetricStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    NOT_MEANINGFUL = "not_meaningful"
    INVALID = "invalid"


@dataclass(frozen=True)
class FinancialSnapshot:
    """Normalised point-in-time inputs for a non-financial company.

    Cash-flow statement outflows such as ``capex`` are supplied as positive
    magnitudes.  All monetary fields must share ``currency`` and ``scale``.
    ``evidence`` maps a field name to one or more immutable evidence IDs.
    """

    period_end: str
    currency: str
    scale: Decimal = Decimal("1")
    revenue: Decimal | None = None
    gross_profit: Decimal | None = None
    ebit: Decimal | None = None
    ebitda: Decimal | None = None
    net_income: Decimal | None = None
    cfo: Decimal | None = None
    capex: Decimal | None = None
    capitalized_software: Decimal = Decimal("0")
    total_assets_begin: Decimal | None = None
    total_assets_end: Decimal | None = None
    invested_capital_begin: Decimal | None = None
    invested_capital_end: Decimal | None = None
    equity_begin: Decimal | None = None
    equity_end: Decimal | None = None
    normalized_tax_rate: Decimal | None = None
    debt: Decimal | None = None
    cash_and_short_term_investments: Decimal | None = None
    preferred_stock: Decimal = Decimal("0")
    minority_interest: Decimal = Decimal("0")
    interest_expense: Decimal | None = None
    diluted_shares: Decimal | None = None
    market_price: Decimal | None = None
    evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        decimal_names = {
            item.name
            for item in fields(self)
            if item.name not in {"period_end", "currency", "evidence"}
        }
        for name in decimal_names:
            object.__setattr__(self, name, to_decimal(getattr(self, name), field_name=name))
        if not self.period_end:
            raise ValueError("period_end is required")
        if not self.currency:
            raise ValueError("currency is required")
        if self.scale is None or self.scale <= 0:
            raise ValueError("scale must be greater than zero")
        if self.normalized_tax_rate is not None and not (
            Decimal("0") <= self.normalized_tax_rate <= Decimal("1")
        ):
            raise ValueError("normalized_tax_rate must be between 0 and 1")
        normalised_evidence = {
            str(key): tuple(str(evidence_id) for evidence_id in value)
            for key, value in self.evidence.items()
        }
        object.__setattr__(self, "evidence", normalised_evidence)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FinancialSnapshot":
        known = {item.name for item in fields(cls)}
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"unknown FinancialSnapshot fields: {sorted(unknown)}")
        data = dict(payload)
        if "evidence" in data:
            data["evidence"] = {
                str(key): tuple(value) if not isinstance(value, str) else (value,)
                for key, value in data["evidence"].items()
            }
        return cls(**data)

    def evidence_for(self, *field_names: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                evidence_id
                for field_name in field_names
                for evidence_id in self.evidence.get(field_name, ())
            )
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, Decimal) or value is None and item.name not in {
                "period_end",
                "currency",
                "evidence",
            }:
                result[item.name] = decimal_to_str(value)
            elif item.name == "evidence":
                result[item.name] = {key: list(ids) for key, ids in value.items()}
            else:
                result[item.name] = value
        return result


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: Decimal | None
    unit: str
    status: MetricStatus
    formula: str
    formula_version: str
    inputs: Mapping[str, Decimal | None]
    evidence_ids: tuple[str, ...] = ()
    explanation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value, field_name=self.name))
        object.__setattr__(
            self,
            "inputs",
            {key: to_decimal(value, field_name=key) for key, value in self.inputs.items()},
        )
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": decimal_to_str(self.value),
            "unit": self.unit,
            "status": self.status.value,
            "formula": self.formula,
            "formula_version": self.formula_version,
            "inputs": {key: decimal_to_str(value) for key, value in self.inputs.items()},
            "evidence_ids": list(self.evidence_ids),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class MetricsReport:
    period_end: str
    currency: str
    formula_registry_version: str
    metrics: Mapping[str, MetricResult]

    def get(self, name: str) -> MetricResult:
        return self.metrics[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_end": self.period_end,
            "currency": self.currency,
            "formula_registry_version": self.formula_registry_version,
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
        }
