"""Input and output contracts for the deterministic valuation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from stock_agent.metrics.models import decimal_to_str, to_decimal


class ScenarioName(str, Enum):
    DOWNSIDE = "downside"
    BASE = "base"
    UPSIDE = "upside"

    @classmethod
    def parse(cls, value: "ScenarioName | str") -> "ScenarioName":
        if isinstance(value, cls):
            return value
        aliases = {
            "bear": cls.DOWNSIDE,
            "bearish": cls.DOWNSIDE,
            "悲观": cls.DOWNSIDE,
            "base": cls.BASE,
            "基准": cls.BASE,
            "bull": cls.UPSIDE,
            "bullish": cls.UPSIDE,
            "乐观": cls.UPSIDE,
        }
        try:
            return aliases[str(value).lower()]
        except KeyError:
            return cls(str(value).lower())


class ValuationMethod(str, Enum):
    FCF_DCF = "fcf_dcf"
    EARNINGS_EXIT_MULTIPLE = "earnings_exit_multiple"


@dataclass(frozen=True)
class ScenarioAssumptions:
    name: ScenarioName
    discount_rate: Decimal
    annual_fcf_growth: Decimal | None = None
    terminal_growth: Decimal | None = None
    annual_earnings_growth: Decimal | None = None
    earnings_exit_multiple: Decimal | None = None
    annual_share_dilution: Decimal = Decimal("0")
    contextual_drivers: Mapping[str, Decimal] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", ScenarioName.parse(self.name))
        for name in (
            "annual_fcf_growth",
            "discount_rate",
            "terminal_growth",
            "annual_earnings_growth",
            "earnings_exit_multiple",
            "annual_share_dilution",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name), field_name=name))
        object.__setattr__(
            self,
            "contextual_drivers",
            {
                str(key): to_decimal(value, field_name=f"contextual_drivers.{key}")
                for key, value in self.contextual_drivers.items()
            },
        )
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioAssumptions":
        return cls(
            name=ScenarioName.parse(payload["name"]),
            discount_rate=payload["discount_rate"],
            annual_fcf_growth=payload.get("annual_fcf_growth"),
            terminal_growth=payload.get("terminal_growth"),
            annual_earnings_growth=payload.get("annual_earnings_growth"),
            earnings_exit_multiple=payload.get("earnings_exit_multiple"),
            annual_share_dilution=payload.get("annual_share_dilution", "0"),
            contextual_drivers=payload.get("contextual_drivers", {}),
            evidence_ids=tuple(payload.get("evidence_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "discount_rate": decimal_to_str(self.discount_rate),
            "annual_fcf_growth": decimal_to_str(self.annual_fcf_growth),
            "terminal_growth": decimal_to_str(self.terminal_growth),
            "annual_earnings_growth": decimal_to_str(self.annual_earnings_growth),
            "earnings_exit_multiple": decimal_to_str(self.earnings_exit_multiple),
            "annual_share_dilution": decimal_to_str(self.annual_share_dilution),
            "contextual_drivers": {
                key: decimal_to_str(value) for key, value in self.contextual_drivers.items()
            },
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class CrossCheckInput:
    method: str
    value_per_share: Decimal
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value_per_share", to_decimal(self.value_per_share, field_name="value_per_share")
        )
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CrossCheckInput":
        return cls(
            method=str(payload["method"]),
            value_per_share=payload["value_per_share"],
            evidence_ids=tuple(payload.get("evidence_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "value_per_share": decimal_to_str(self.value_per_share),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ValuationRequest:
    security_id: str
    as_of: str
    currency: str
    current_price: Decimal
    shares_outstanding: Decimal
    scenarios: tuple[ScenarioAssumptions, ...]
    method: ValuationMethod = ValuationMethod.FCF_DCF
    starting_fcf: Decimal | None = None
    starting_earnings: Decimal | None = None
    net_debt: Decimal = Decimal("0")
    forecast_years: int = 5
    cumulative_dividends_per_share: Decimal = Decimal("0")
    required_margin_of_safety: Decimal = Decimal("0.25")
    overvaluation_premium: Decimal = Decimal("0.20")
    cross_checks: tuple[CrossCheckInput, ...] = ()
    cross_check_tolerance: Decimal = Decimal("0.25")
    evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "current_price",
            "starting_fcf",
            "starting_earnings",
            "shares_outstanding",
            "net_debt",
            "cumulative_dividends_per_share",
            "required_margin_of_safety",
            "overvaluation_premium",
            "cross_check_tolerance",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name), field_name=name))
        if not isinstance(self.method, ValuationMethod):
            object.__setattr__(self, "method", ValuationMethod(str(self.method).lower()))
        object.__setattr__(
            self,
            "scenarios",
            tuple(
                item if isinstance(item, ScenarioAssumptions) else ScenarioAssumptions.from_dict(item)
                for item in self.scenarios
            ),
        )
        object.__setattr__(
            self,
            "cross_checks",
            tuple(
                item if isinstance(item, CrossCheckInput) else CrossCheckInput.from_dict(item)
                for item in self.cross_checks
            ),
        )
        object.__setattr__(
            self,
            "evidence",
            {
                str(key): tuple(value) if not isinstance(value, str) else (value,)
                for key, value in self.evidence.items()
            },
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ValuationRequest":
        return cls(
            security_id=str(payload["security_id"]),
            as_of=str(payload["as_of"]),
            currency=str(payload["currency"]),
            current_price=payload["current_price"],
            shares_outstanding=payload["shares_outstanding"],
            scenarios=tuple(
                item if isinstance(item, ScenarioAssumptions) else ScenarioAssumptions.from_dict(item)
                for item in payload["scenarios"]
            ),
            method=ValuationMethod(str(payload.get("method", "fcf_dcf")).lower()),
            starting_fcf=payload.get("starting_fcf"),
            starting_earnings=payload.get("starting_earnings", payload.get("normalized_earnings")),
            net_debt=payload.get("net_debt", "0"),
            forecast_years=int(payload.get("forecast_years", 5)),
            cumulative_dividends_per_share=payload.get("cumulative_dividends_per_share", "0"),
            required_margin_of_safety=payload.get("required_margin_of_safety", "0.25"),
            overvaluation_premium=payload.get("overvaluation_premium", "0.20"),
            cross_checks=tuple(
                item if isinstance(item, CrossCheckInput) else CrossCheckInput.from_dict(item)
                for item in payload.get("cross_checks", ())
            ),
            cross_check_tolerance=payload.get("cross_check_tolerance", "0.25"),
            evidence=payload.get("evidence", {}),
        )

    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                evidence_id
                for ids in self.evidence.values()
                for evidence_id in ids
            )
        )


@dataclass(frozen=True)
class ScenarioValuation:
    name: ScenarioName
    valuation_method: ValuationMethod
    enterprise_value: Decimal
    equity_value: Decimal
    diluted_shares_at_horizon: Decimal
    intrinsic_value_per_share: Decimal
    margin_of_safety: Decimal | None
    expected_annual_return: Decimal | None
    projected_fcfs: tuple[Decimal, ...]
    projected_earnings: tuple[Decimal, ...]
    present_values: tuple[Decimal, ...]
    terminal_value: Decimal
    present_value_of_terminal: Decimal
    terminal_value_per_share: Decimal
    assumptions: ScenarioAssumptions
    formulas: Mapping[str, str]
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioValuation":
        return cls(
            name=ScenarioName.parse(payload["name"]),
            valuation_method=ValuationMethod(payload.get("valuation_method", "fcf_dcf")),
            enterprise_value=to_decimal(payload["enterprise_value"]),
            equity_value=to_decimal(payload["equity_value"]),
            diluted_shares_at_horizon=to_decimal(payload["diluted_shares_at_horizon"]),
            intrinsic_value_per_share=to_decimal(payload["intrinsic_value_per_share"]),
            margin_of_safety=to_decimal(payload.get("margin_of_safety")),
            expected_annual_return=to_decimal(payload.get("expected_annual_return")),
            projected_fcfs=tuple(to_decimal(value) for value in payload["projected_fcfs"]),
            projected_earnings=tuple(
                to_decimal(value) for value in payload.get("projected_earnings", ())
            ),
            present_values=tuple(to_decimal(value) for value in payload["present_values"]),
            terminal_value=to_decimal(payload["terminal_value"]),
            present_value_of_terminal=to_decimal(payload["present_value_of_terminal"]),
            terminal_value_per_share=to_decimal(
                payload.get("terminal_value_per_share", payload["intrinsic_value_per_share"])
            ),
            assumptions=ScenarioAssumptions.from_dict(payload["assumptions"]),
            formulas=dict(payload["formulas"]),
            evidence_ids=tuple(payload.get("evidence_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "valuation_method": self.valuation_method.value,
            "enterprise_value": decimal_to_str(self.enterprise_value),
            "equity_value": decimal_to_str(self.equity_value),
            "diluted_shares_at_horizon": decimal_to_str(self.diluted_shares_at_horizon),
            "intrinsic_value_per_share": decimal_to_str(self.intrinsic_value_per_share),
            "margin_of_safety": decimal_to_str(self.margin_of_safety),
            "expected_annual_return": decimal_to_str(self.expected_annual_return),
            "projected_fcfs": [decimal_to_str(value) for value in self.projected_fcfs],
            "projected_earnings": [decimal_to_str(value) for value in self.projected_earnings],
            "present_values": [decimal_to_str(value) for value in self.present_values],
            "terminal_value": decimal_to_str(self.terminal_value),
            "present_value_of_terminal": decimal_to_str(self.present_value_of_terminal),
            "terminal_value_per_share": decimal_to_str(self.terminal_value_per_share),
            "assumptions": self.assumptions.to_dict(),
            "formulas": dict(self.formulas),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class CrossValidationResult:
    available: bool
    passed: bool
    reference_value_per_share: Decimal | None
    relative_gap: Decimal | None
    tolerance: Decimal
    methods: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    explanation: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CrossValidationResult":
        return cls(
            available=bool(payload["available"]),
            passed=bool(payload["passed"]),
            reference_value_per_share=to_decimal(payload.get("reference_value_per_share")),
            relative_gap=to_decimal(payload.get("relative_gap")),
            tolerance=to_decimal(payload["tolerance"]),
            methods=tuple(payload.get("methods", ())),
            evidence_ids=tuple(payload.get("evidence_ids", ())),
            explanation=str(payload["explanation"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "passed": self.passed,
            "reference_value_per_share": decimal_to_str(self.reference_value_per_share),
            "relative_gap": decimal_to_str(self.relative_gap),
            "tolerance": decimal_to_str(self.tolerance),
            "methods": list(self.methods),
            "evidence_ids": list(self.evidence_ids),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class PriceBands:
    watch_price: Decimal
    entry_price_ceiling: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    expensive_price: Decimal

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PriceBands":
        return cls(**{key: to_decimal(value) for key, value in payload.items()})

    def to_dict(self) -> dict[str, str | None]:
        return {
            "watch_price": decimal_to_str(self.watch_price),
            "entry_price_ceiling": decimal_to_str(self.entry_price_ceiling),
            "fair_value_low": decimal_to_str(self.fair_value_low),
            "fair_value_high": decimal_to_str(self.fair_value_high),
            "expensive_price": decimal_to_str(self.expensive_price),
        }


@dataclass(frozen=True)
class ValuationReport:
    security_id: str
    as_of: str
    currency: str
    current_price: Decimal
    forecast_years: int
    valid: bool
    errors: tuple[str, ...]
    scenarios: Mapping[ScenarioName, ScenarioValuation]
    price_bands: PriceBands | None
    cross_validation: CrossValidationResult
    formula_version: str
    input_evidence_ids: tuple[str, ...]

    @property
    def base(self) -> ScenarioValuation | None:
        return self.scenarios.get(ScenarioName.BASE)

    @property
    def intrinsic_value_range(self) -> tuple[Decimal, Decimal] | None:
        if not self.scenarios:
            return None
        values = [scenario.intrinsic_value_per_share for scenario in self.scenarios.values()]
        return min(values), max(values)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ValuationReport":
        scenarios: dict[ScenarioName, ScenarioValuation] = {}
        raw_scenarios = payload.get("scenarios", {})
        values: Sequence[Mapping[str, Any]]
        if isinstance(raw_scenarios, Mapping):
            values = list(raw_scenarios.values())
        else:
            values = list(raw_scenarios)
        for raw_scenario in values:
            scenario = ScenarioValuation.from_dict(raw_scenario)
            scenarios[scenario.name] = scenario
        return cls(
            security_id=str(payload["security_id"]),
            as_of=str(payload["as_of"]),
            currency=str(payload["currency"]),
            current_price=to_decimal(payload["current_price"]),
            forecast_years=int(payload["forecast_years"]),
            valid=bool(payload["valid"]),
            errors=tuple(payload.get("errors", ())),
            scenarios=scenarios,
            price_bands=(
                PriceBands.from_dict(payload["price_bands"])
                if payload.get("price_bands") is not None
                else None
            ),
            cross_validation=CrossValidationResult.from_dict(payload["cross_validation"]),
            formula_version=str(payload["formula_version"]),
            input_evidence_ids=tuple(payload.get("input_evidence_ids", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "as_of": self.as_of,
            "currency": self.currency,
            "current_price": decimal_to_str(self.current_price),
            "forecast_years": self.forecast_years,
            "valid": self.valid,
            "errors": list(self.errors),
            "scenarios": {
                name.value: scenario.to_dict() for name, scenario in self.scenarios.items()
            },
            "intrinsic_value_range": (
                [decimal_to_str(value) for value in self.intrinsic_value_range]
                if self.intrinsic_value_range
                else None
            ),
            "price_bands": self.price_bands.to_dict() if self.price_bands else None,
            "cross_validation": self.cross_validation.to_dict(),
            "formula_version": self.formula_version,
            "input_evidence_ids": list(self.input_evidence_ids),
        }
