"""Rule-based value scorecard combining quality, valuation and uncertainty."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from stock_agent.valuation import ScenarioName, ValuationReport

from .models import DimensionScore, FactorScore, ValueScorecard


ASSESSMENT_VERSION = "value_scorecard.v2"

DIMENSION_WEIGHTS: dict[str, Decimal] = {
    "profitability": Decimal("0.20"),
    "growth": Decimal("0.15"),
    "cashflow": Decimal("0.15"),
    "balance_sheet": Decimal("0.15"),
    "capital_allocation": Decimal("0.10"),
    "valuation": Decimal("0.25"),
}

DIMENSION_LABELS = {
    "profitability": "盈利能力",
    "growth": "增长质量",
    "cashflow": "现金流",
    "balance_sheet": "资产负债表",
    "capital_allocation": "资本配置",
    "valuation": "估值吸引力",
}

QUALITY_DIMENSIONS = tuple(
    dimension for dimension in DIMENSION_WEIGHTS if dimension != "valuation"
)
QUALITY_WEIGHT = sum(
    (DIMENSION_WEIGHTS[dimension] for dimension in QUALITY_DIMENSIONS),
    Decimal("0"),
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(high, max(low, value))


def _linear_score(value: Decimal, *, bad: Decimal, good: Decimal) -> Decimal:
    if good == bad:
        raise ValueError("assessment factor good and bad thresholds must differ")
    score = (value - bad) / (good - bad) * Decimal("100")
    return _clamp(score, Decimal("0"), Decimal("100"))


class AssessmentEngine:
    """Create a deterministic scorecard from reviewed facts and valuation."""

    def compute(
        self,
        financial: Mapping[str, Any],
        valuation: ValuationReport,
    ) -> ValueScorecard:
        config = financial.get("assessment", {})
        if not isinstance(config, Mapping):
            raise ValueError("assessment configuration must be an object")
        facts = financial.get("facts", {})
        if not isinstance(facts, Mapping):
            raise ValueError("financial facts must be an object")

        configured: dict[str, list[FactorScore]] = {
            dimension: [] for dimension in QUALITY_DIMENSIONS
        }
        raw_factors = config.get("factors", ())
        if not isinstance(raw_factors, (list, tuple)):
            raise ValueError("assessment factors must be a list")
        for raw in raw_factors:
            if not isinstance(raw, Mapping):
                raise ValueError("each assessment factor must be an object")
            factor = self._configured_factor(raw, facts)
            if factor.dimension not in configured:
                raise ValueError(
                    f"unsupported assessment dimension: {factor.dimension}"
                )
            configured[factor.dimension].append(factor)

        dimensions: list[DimensionScore] = []
        for dimension in QUALITY_DIMENSIONS:
            dimensions.append(self._dimension(dimension, configured[dimension]))

        valuation_dimension, dispersion = self._valuation_dimension(valuation)
        dimensions.append(valuation_dimension)

        quality_score, quality_coverage = self._aggregate(
            dimensions, include=set(QUALITY_DIMENSIONS), total_weight=QUALITY_WEIGHT
        )
        raw_composite, overall_coverage = self._aggregate(
            dimensions,
            include=set(DIMENSION_WEIGHTS),
            total_weight=Decimal("1"),
        )
        risk_penalty, hard_risk = self._risk_penalty(financial)
        composite = _clamp(
            raw_composite - risk_penalty, Decimal("0"), Decimal("100")
        )

        minimum_quality = _decimal(config.get("minimum_quality_score")) or Decimal(
            "60"
        )
        minimum_buy = _decimal(config.get("minimum_buy_score")) or Decimal("70")
        minimum_coverage = _decimal(config.get("minimum_coverage")) or Decimal(
            "0.65"
        )
        confidence = str(financial.get("confidence", "medium")).lower()
        confidence_buffer = {
            "high": Decimal("0"),
            "medium": Decimal("0.03"),
            "low": Decimal("0.07"),
            "insufficient": Decimal("0.10"),
        }.get(confidence, Decimal("0.05"))
        dispersion_buffer = min(
            Decimal("0.07"),
            (dispersion or Decimal("0")) * Decimal("0.05"),
        )
        valuation_config = financial.get("valuation", {})
        if not isinstance(valuation_config, Mapping):
            raise ValueError("valuation configuration must be an object")
        base_margin = _decimal(valuation_config.get("margin_of_safety")) or Decimal(
            "0.25"
        )
        base_return = _decimal(valuation_config.get("minimum_buy_return")) or Decimal(
            "0.12"
        )
        adjusted_margin = min(
            Decimal("0.50"), base_margin + confidence_buffer + dispersion_buffer
        ).quantize(Decimal("0.001"))
        adjusted_return = min(
            Decimal("0.30"),
            base_return + (confidence_buffer + dispersion_buffer) / Decimal("2"),
        ).quantize(Decimal("0.001"))

        quality_qualified = (
            quality_score >= minimum_quality
            and quality_coverage >= minimum_coverage
            and not hard_risk
        )
        notes = (
            "缺失维度按中性 50 分计入总分，同时降低覆盖率；不会因缺数据获得高分。",
            "综合分用于新增风险敞口门槛，不能绕过数据质量、重大风险和估值安全边际。",
            "中低置信度及三情景离散度会提高安全边际和目标回报要求。",
        )
        return ValueScorecard(
            version=ASSESSMENT_VERSION,
            quality_score=quality_score.quantize(Decimal("0.1")),
            valuation_score=valuation_dimension.score or Decimal("50"),
            composite_score=composite.quantize(Decimal("0.1")),
            risk_penalty=risk_penalty,
            quality_coverage=quality_coverage.quantize(Decimal("0.001")),
            overall_coverage=overall_coverage.quantize(Decimal("0.001")),
            minimum_quality_score=minimum_quality,
            minimum_buy_score=minimum_buy,
            minimum_coverage=minimum_coverage,
            quality_qualified=quality_qualified,
            buy_score_passed=composite >= minimum_buy,
            scenario_dispersion=(
                dispersion.quantize(Decimal("0.001")) if dispersion is not None else None
            ),
            adjusted_margin_of_safety=adjusted_margin,
            adjusted_target_return=adjusted_return,
            dimensions=tuple(dimensions),
            notes=notes,
        )

    def _configured_factor(
        self, raw: Mapping[str, Any], facts: Mapping[str, Any]
    ) -> FactorScore:
        factor_id = str(raw.get("factor_id") or "").strip()
        label = str(raw.get("label") or factor_id).strip()
        dimension = str(raw.get("dimension") or "").strip()
        if not factor_id or not label or not dimension:
            raise ValueError("assessment factor requires id, label and dimension")
        value, source = self._resolve_value(raw, facts)
        weight = _decimal(raw.get("weight")) or Decimal("1")
        if weight <= 0:
            raise ValueError(f"assessment factor {factor_id} weight must be positive")
        bad = _decimal(raw.get("bad"))
        good = _decimal(raw.get("good"))
        if bad is None or good is None:
            raise ValueError(f"assessment factor {factor_id} requires bad and good")
        score = _linear_score(value, bad=bad, good=good) if value is not None else None
        direction = "越高越好" if good > bad else "越低越好"
        explanation = (
            f"{direction}；0 分阈值 {bad}，100 分阈值 {good}。"
            if value is not None
            else f"缺少 {source}，该因子不打分并降低覆盖率。"
        )
        return FactorScore(
            factor_id=factor_id,
            label=label,
            dimension=dimension,
            value=value,
            score=score,
            weight=weight,
            source=source,
            explanation=explanation,
        )

    @staticmethod
    def _resolve_value(
        raw: Mapping[str, Any], facts: Mapping[str, Any]
    ) -> tuple[Decimal | None, str]:
        fact = raw.get("fact")
        if fact:
            key = str(fact)
            return _decimal(facts.get(key)), f"facts.{key}"
        average_facts = raw.get("average_facts")
        if isinstance(average_facts, (list, tuple)) and average_facts:
            keys = tuple(str(item) for item in average_facts)
            values = tuple(_decimal(facts.get(key)) for key in keys)
            if any(value is None for value in values):
                return None, "average(" + ",".join(f"facts.{key}" for key in keys) + ")"
            return sum((value for value in values if value is not None), Decimal("0")) / Decimal(
                len(values)
            ), "average(" + ",".join(f"facts.{key}" for key in keys) + ")"
        numerator = raw.get("numerator_fact")
        denominator = raw.get("denominator_fact")
        if numerator and denominator:
            numerator_key, denominator_key = str(numerator), str(denominator)
            top = _decimal(facts.get(numerator_key))
            bottom = _decimal(facts.get(denominator_key))
            source = f"facts.{numerator_key} / facts.{denominator_key}"
            if top is None or bottom is None or bottom == 0:
                return None, source
            return top / bottom, source
        raise ValueError("assessment factor requires fact, average_facts or ratio facts")

    @staticmethod
    def _dimension(dimension: str, factors: list[FactorScore]) -> DimensionScore:
        total_weight = sum((factor.weight for factor in factors), Decimal("0"))
        scored_weight = sum(
            (factor.weight for factor in factors if factor.score is not None),
            Decimal("0"),
        )
        score = None
        if scored_weight > 0:
            score = sum(
                (
                    factor.score * factor.weight
                    for factor in factors
                    if factor.score is not None
                ),
                Decimal("0"),
            ) / scored_weight
            score = score.quantize(Decimal("0.1"))
        coverage = scored_weight / total_weight if total_weight > 0 else Decimal("0")
        return DimensionScore(
            dimension=dimension,
            label=DIMENSION_LABELS[dimension],
            score=score,
            weight=DIMENSION_WEIGHTS[dimension],
            coverage=coverage.quantize(Decimal("0.001")),
            factors=tuple(factors),
        )

    def _valuation_dimension(
        self, valuation: ValuationReport
    ) -> tuple[DimensionScore, Decimal | None]:
        base = valuation.base
        downside = valuation.scenarios.get(ScenarioName.DOWNSIDE)
        upside = valuation.scenarios.get(ScenarioName.UPSIDE)
        factors: list[FactorScore] = []
        definitions = (
            (
                "margin_of_safety",
                "基准安全边际",
                base.margin_of_safety if base else None,
                Decimal("-0.10"),
                Decimal("0.40"),
            ),
            (
                "base_expected_return",
                "基准预期年化回报",
                base.expected_annual_return if base else None,
                Decimal("0"),
                Decimal("0.20"),
            ),
            (
                "downside_expected_return",
                "悲观情景年化回报",
                downside.expected_annual_return if downside else None,
                Decimal("-0.15"),
                Decimal("0.08"),
            ),
        )
        for factor_id, label, value, bad, good in definitions:
            score = _linear_score(value, bad=bad, good=good) if value is not None else None
            factors.append(
                FactorScore(
                    factor_id=factor_id,
                    label=label,
                    dimension="valuation",
                    value=value,
                    score=score,
                    weight=Decimal("1"),
                    source="valuation.scenarios",
                    explanation=f"越高越好；0 分阈值 {bad}，100 分阈值 {good}。",
                )
            )
        dimension = self._dimension("valuation", factors)
        dispersion = None
        if (
            base is not None
            and downside is not None
            and upside is not None
            and base.intrinsic_value_per_share > 0
        ):
            dispersion = (
                upside.intrinsic_value_per_share
                - downside.intrinsic_value_per_share
            ) / base.intrinsic_value_per_share
            dispersion = max(Decimal("0"), dispersion)
        return dimension, dispersion

    @staticmethod
    def _aggregate(
        dimensions: list[DimensionScore],
        *,
        include: set[str],
        total_weight: Decimal,
    ) -> tuple[Decimal, Decimal]:
        weighted_score = Decimal("0")
        covered_weight = Decimal("0")
        for dimension in dimensions:
            if dimension.dimension not in include:
                continue
            # Missing dimensions contribute a neutral score but no coverage.
            weighted_score += (dimension.score or Decimal("50")) * dimension.weight
            covered_weight += dimension.weight * dimension.coverage
        return weighted_score / total_weight, covered_weight / total_weight

    @staticmethod
    def _risk_penalty(financial: Mapping[str, Any]) -> tuple[Decimal, bool]:
        penalty = Decimal("0")
        hard_risk = False
        signals = financial.get("signals", ())
        if isinstance(signals, (list, tuple)):
            for signal in signals:
                if not isinstance(signal, Mapping):
                    continue
                severity = str(signal.get("severity", "")).lower()
                if severity in {"watch", "yellow", "warning"}:
                    penalty += Decimal("2")
                elif severity == "orange":
                    penalty += Decimal("5")
                elif severity in {"red", "critical"}:
                    penalty += Decimal("15")
                    hard_risk = True
        red_flags = financial.get("red_flags", ())
        if isinstance(red_flags, (list, tuple)) and red_flags:
            penalty += Decimal("15") * Decimal(len(red_flags))
            hard_risk = True
        return min(Decimal("20"), penalty), hard_risk


__all__ = ["ASSESSMENT_VERSION", "AssessmentEngine"]
