"""Rule-only investment recommendation engine.

This module never calls an LLM.  The same valuation snapshot, quality flags
and policy version always produce the same action and audit trace.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from stock_agent.metrics.models import decimal_to_str
from stock_agent.valuation import DCF_FORMULAS, EARNINGS_EXIT_FORMULAS, ScenarioName

from .gates import evaluate_data_quality
from .models import (
    Confidence,
    RecommendationAction,
    RecommendationRequest,
    RecommendationResult,
    RiskSeverity,
    RuleEvaluation,
    ThesisStatus,
)


RECOMMENDATION_FORMULAS = {
    **DCF_FORMULAS,
    **{f"earnings_{key}": value for key, value in EARNINGS_EXIT_FORMULAS.items()},
    "entry_price_ceiling": "base_intrinsic_value * (1 - required_margin_of_safety)",
    "buy_candidate": "quality_pass AND thesis_valid AND no_red_risk AND confidence>=minimum AND cross_validated AND composite_score>=minimum_buy_score AND price<=entry_ceiling AND base_return>=target_return",
    "target_position_ceiling": "not calculated in company-level mode; requires a complete investor profile",
}


class RecommendationEngine:
    def recommend(
        self, raw: RecommendationRequest | Mapping[str, object]
    ) -> RecommendationResult:
        request = (
            raw if isinstance(raw, RecommendationRequest) else RecommendationRequest.from_dict(raw)
        )
        quality = evaluate_data_quality(request.data_quality)
        valuation = request.valuation
        red_events = tuple(
            event
            for event in request.risk_events
            if event.triggered and event.severity is RiskSeverity.RED
        )
        all_evidence_ids = self._evidence_ids(request)
        trace: list[RuleEvaluation] = []

        trace.append(
            RuleEvaluation(
                rule_id="no_red_hard_risk",
                passed=not red_events,
                actual=",".join(event.code for event in red_events) or "none",
                threshold="no triggered red event",
                explanation="red audit, liquidity, default or governance events override valuation",
                evidence_ids=tuple(
                    evidence_id for event in red_events for evidence_id in event.evidence_ids
                ),
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="thesis_valid",
                passed=request.thesis_status is ThesisStatus.VALID,
                actual=request.thesis_status.value,
                threshold=ThesisStatus.VALID.value,
                explanation="an invalid or unverified thesis cannot support a positive view",
                evidence_ids=request.supporting_evidence_ids,
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="data_quality_gate",
                passed=quality.passed,
                actual=",".join(issue.code for issue in quality.blockers) or "passed",
                threshold="all fail-closed assertions pass",
                explanation="stale, missing or conflicting critical data freezes positive advice",
                evidence_ids=tuple(
                    evidence_id for issue in quality.blockers for evidence_id in issue.evidence_ids
                ),
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="valuation_valid",
                passed=valuation.valid and valuation.base is not None,
                actual=",".join(valuation.errors) or "valid",
                threshold="valid three-scenario valuation",
                explanation="the applicable valuation model must produce an ordered three-scenario result",
                evidence_ids=valuation.input_evidence_ids,
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="investment_case_qualified",
                passed=request.investment_case_qualified,
                actual=str(request.investment_case_qualified).lower(),
                threshold="true",
                explanation="the business and financial-quality screen must pass",
                evidence_ids=request.supporting_evidence_ids,
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="minimum_confidence",
                passed=request.confidence.rank >= request.policy.minimum_confidence.rank,
                actual=request.confidence.value,
                threshold=request.policy.minimum_confidence.value,
                explanation="low-confidence output cannot trigger a new positive action",
                evidence_ids=request.supporting_evidence_ids,
            )
        )
        cross_validation_passed = (
            not request.policy.require_cross_validation or valuation.cross_validation.passed
        )
        trace.append(
            RuleEvaluation(
                rule_id="valuation_cross_validation",
                passed=cross_validation_passed,
                actual=(
                    decimal_to_str(valuation.cross_validation.relative_gap)
                    if valuation.cross_validation.available
                    else "unavailable"
                ),
                threshold=(
                    decimal_to_str(valuation.cross_validation.tolerance)
                    if request.policy.require_cross_validation
                    else "not required"
                ),
                explanation=valuation.cross_validation.explanation,
                evidence_ids=valuation.cross_validation.evidence_ids,
            )
        )
        score_passed = (
            request.composite_score is None
            or request.composite_score >= request.minimum_buy_score
        )
        trace.append(
            RuleEvaluation(
                rule_id="minimum_composite_score",
                passed=score_passed,
                actual=(
                    decimal_to_str(request.composite_score)
                    if request.composite_score is not None
                    else "not supplied"
                ),
                threshold=decimal_to_str(request.minimum_buy_score),
                explanation=(
                    "the explainable quality, valuation and risk score must clear "
                    "the new-exposure threshold"
                ),
                evidence_ids=request.supporting_evidence_ids,
            )
        )

        action: RecommendationAction
        rationale: list[str]
        if red_events or request.thesis_status is ThesisStatus.INVALID:
            action = RecommendationAction.RISK_AVOIDANCE
            reasons = [event.description for event in red_events]
            if request.thesis_status is ThesisStatus.INVALID:
                reasons.append("the pre-defined investment thesis invalidation condition was triggered")
            rationale = reasons
        elif not quality.passed:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = [f"data-quality blocker: {issue.message}" for issue in quality.blockers]
        elif not valuation.valid or valuation.base is None or valuation.price_bands is None:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = [f"valuation blocker: {error}" for error in valuation.errors] or [
                "the valuation result is incomplete"
            ]
        elif request.thesis_status is ThesisStatus.UNKNOWN:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = ["the investment thesis has not been verified"]
        elif not request.investment_case_qualified:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = ["the financial-quality and business-quality screen did not pass"]
        elif request.confidence.rank < request.policy.minimum_confidence.rank:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = [
                f"confidence {request.confidence.value} is below the policy minimum "
                f"{request.policy.minimum_confidence.value}"
            ]
        elif request.policy.require_cross_validation and not valuation.cross_validation.passed:
            action = RecommendationAction.NO_RECOMMENDATION
            rationale = [valuation.cross_validation.explanation]
        else:
            action, rationale = self._price_and_return_rules(request, trace)

        suppress_valuation = bool(red_events or request.thesis_status is ThesisStatus.INVALID) and not quality.passed
        scenario_values = None
        expected_returns = None
        margin_of_safety = None
        price_bands = None
        suppression_reason = None
        if suppress_valuation:
            suppression_reason = (
                "hard risk is present while critical data is unreliable; precise valuation is suppressed"
            )
        elif valuation.valid and valuation.base is not None and valuation.price_bands is not None:
            scenario_values = {
                name: scenario.intrinsic_value_per_share
                for name, scenario in valuation.scenarios.items()
            }
            expected_returns = {
                name: scenario.expected_annual_return
                for name, scenario in valuation.scenarios.items()
            }
            margin_of_safety = valuation.base.margin_of_safety
            price_bands = {
                "watch_price": valuation.price_bands.watch_price,
                "entry_price_ceiling": valuation.base.intrinsic_value_per_share
                * (Decimal("1") - request.policy.required_margin_of_safety),
                "fair_value_low": valuation.price_bands.fair_value_low,
                "fair_value_high": valuation.price_bands.fair_value_high,
                "expensive_price": valuation.price_bands.expensive_price,
            }

        return RecommendationResult(
            security_id=valuation.security_id,
            company_name=request.company_name,
            as_of=valuation.as_of,
            scope="company_research",
            action=action,
            confidence=request.confidence,
            currency=valuation.currency,
            current_price=valuation.current_price,
            scenario_values=scenario_values,
            margin_of_safety=margin_of_safety,
            expected_annual_returns=expected_returns,
            price_bands=price_bands,
            valuation_suppressed_reason=suppression_reason,
            data_quality=quality,
            rationale=tuple(rationale),
            rule_trace=tuple(trace),
            risk_events=request.risk_events,
            evidence=request.evidence,
            evidence_ids=all_evidence_ids,
            catalysts=request.catalysts,
            risks=request.risks,
            invalidation_conditions=request.invalidation_conditions,
            valid_until=request.valid_until,
            next_review_date=request.next_review_date,
            policy=request.policy,
            formula_audit={
                **RECOMMENDATION_FORMULAS,
                "valuation_formula_version": valuation.formula_version,
                "recommendation_policy_version": request.policy.version,
            },
            disclaimer=(
                "个人研究用途的条件性公司观点，不构成面向第三方的证券投资咨询，"
                "不包含自动交易或收益承诺。"
            ),
        )

    @staticmethod
    def _price_and_return_rules(
        request: RecommendationRequest, trace: list[RuleEvaluation]
    ) -> tuple[RecommendationAction, list[str]]:
        valuation = request.valuation
        base = valuation.base
        assert base is not None
        assert valuation.price_bands is not None

        policy_entry_ceiling = base.intrinsic_value_per_share * (
            Decimal("1") - request.policy.required_margin_of_safety
        )
        margin_passed = (
            valuation.current_price <= policy_entry_ceiling
            and base.margin_of_safety is not None
            and base.margin_of_safety >= request.policy.required_margin_of_safety
        )
        return_passed = (
            base.expected_annual_return is not None
            and base.expected_annual_return >= request.policy.target_annual_return
        )
        trace.append(
            RuleEvaluation(
                rule_id="required_margin_of_safety",
                passed=margin_passed,
                actual=decimal_to_str(base.margin_of_safety) or "N/M",
                threshold=decimal_to_str(request.policy.required_margin_of_safety),
                explanation=f"policy entry ceiling is {policy_entry_ceiling}",
                evidence_ids=base.evidence_ids,
            )
        )
        trace.append(
            RuleEvaluation(
                rule_id="target_annual_return",
                passed=return_passed,
                actual=decimal_to_str(base.expected_annual_return) or "N/M",
                threshold=decimal_to_str(request.policy.target_annual_return),
                explanation="base-case annualised total return must meet the configured target",
                evidence_ids=base.evidence_ids,
            )
        )
        score_passed = (
            request.composite_score is None
            or request.composite_score >= request.minimum_buy_score
        )
        if margin_passed and return_passed and score_passed:
            return RecommendationAction.BUY_CANDIDATE, [
                "the thesis and quality gates passed with no red hard-risk override",
                (
                    f"current price {valuation.current_price} is at or below the policy entry ceiling "
                    f"{policy_entry_ceiling}"
                ),
                (
                    f"base annualised return {base.expected_annual_return} meets the target "
                    f"{request.policy.target_annual_return}"
                ),
            ]

        if margin_passed and return_passed and not score_passed:
            return RecommendationAction.WAIT, [
                "valuation gates passed, but the composite quality and risk score is below the new-exposure threshold",
                (
                    f"composite score {request.composite_score} is below "
                    f"{request.minimum_buy_score}"
                ),
            ]

        if request.existing_position:
            below_hold_return = (
                base.expected_annual_return is None
                or base.expected_annual_return < request.policy.minimum_hold_annual_return
            )
            expensive = valuation.current_price >= valuation.price_bands.expensive_price
            trace.append(
                RuleEvaluation(
                    rule_id="reduce_threshold",
                    passed=not (below_hold_return or expensive),
                    actual=(
                        f"base_return={decimal_to_str(base.expected_annual_return) or 'N/M'},"
                        f"price={valuation.current_price}"
                    ),
                    threshold=(
                        f"minimum_hold_return={request.policy.minimum_hold_annual_return},"
                        f"expensive_price={valuation.price_bands.expensive_price}"
                    ),
                    explanation="a held security becomes a reduction candidate when return is too low or price is expensive",
                    evidence_ids=base.evidence_ids,
                )
            )
            if below_hold_return or expensive:
                return RecommendationAction.REDUCE_CANDIDATE, [
                    "the thesis remains valid, but the current risk/reward is below the hold threshold"
                ]
            return RecommendationAction.HOLD, [
                "the thesis remains valid and expected return exceeds the hold threshold",
                "the price or return does not meet the stricter add/buy threshold",
            ]

        return RecommendationAction.WAIT, [
            "the thesis and quality gates passed",
            "the required safety margin or target annual return is not yet available at this price",
        ]

    @staticmethod
    def _evidence_ids(request: RecommendationRequest) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                request.supporting_evidence_ids
                + request.valuation.input_evidence_ids
                + request.valuation.cross_validation.evidence_ids
                + tuple(
                    evidence_id
                    for event in request.risk_events
                    for evidence_id in event.evidence_ids
                )
                + tuple(item.evidence_id for item in request.evidence)
            )
        )
