"""Output contracts for the value-investing scorecard."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from stock_agent.metrics.models import decimal_to_str


@dataclass(frozen=True, slots=True)
class FactorScore:
    factor_id: str
    label: str
    dimension: str
    value: Decimal | None
    score: Decimal | None
    weight: Decimal
    source: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "label": self.label,
            "dimension": self.dimension,
            "value": decimal_to_str(self.value),
            "score": decimal_to_str(self.score),
            "weight": decimal_to_str(self.weight),
            "source": self.source,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class DimensionScore:
    dimension: str
    label: str
    score: Decimal | None
    weight: Decimal
    coverage: Decimal
    factors: tuple[FactorScore, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "score": decimal_to_str(self.score),
            "weight": decimal_to_str(self.weight),
            "coverage": decimal_to_str(self.coverage),
            "factors": [factor.to_dict() for factor in self.factors],
        }


@dataclass(frozen=True, slots=True)
class ValueScorecard:
    version: str
    quality_score: Decimal
    valuation_score: Decimal
    composite_score: Decimal
    risk_penalty: Decimal
    quality_coverage: Decimal
    overall_coverage: Decimal
    minimum_quality_score: Decimal
    minimum_buy_score: Decimal
    minimum_coverage: Decimal
    quality_qualified: bool
    buy_score_passed: bool
    scenario_dispersion: Decimal | None
    adjusted_margin_of_safety: Decimal
    adjusted_target_return: Decimal
    dimensions: tuple[DimensionScore, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "quality_score": decimal_to_str(self.quality_score),
            "valuation_score": decimal_to_str(self.valuation_score),
            "composite_score": decimal_to_str(self.composite_score),
            "risk_penalty": decimal_to_str(self.risk_penalty),
            "quality_coverage": decimal_to_str(self.quality_coverage),
            "overall_coverage": decimal_to_str(self.overall_coverage),
            "minimum_quality_score": decimal_to_str(self.minimum_quality_score),
            "minimum_buy_score": decimal_to_str(self.minimum_buy_score),
            "minimum_coverage": decimal_to_str(self.minimum_coverage),
            "quality_qualified": self.quality_qualified,
            "buy_score_passed": self.buy_score_passed,
            "scenario_dispersion": decimal_to_str(self.scenario_dispersion),
            "adjusted_margin_of_safety": decimal_to_str(
                self.adjusted_margin_of_safety
            ),
            "adjusted_target_return": decimal_to_str(self.adjusted_target_return),
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "notes": list(self.notes),
        }
