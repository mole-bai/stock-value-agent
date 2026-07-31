"""A reproducible three-scenario free-cash-flow DCF engine."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Mapping

from .models import (
    CrossValidationResult,
    PriceBands,
    ScenarioAssumptions,
    ScenarioName,
    ScenarioValuation,
    ValuationMethod,
    ValuationReport,
    ValuationRequest,
)


VALUATION_FORMULA_VERSION = "fcf_dcf_three_scenario.v1"
EARNINGS_VALUATION_FORMULA_VERSION = "earnings_exit_multiple_three_scenario.v1"


DCF_FORMULAS = {
    "projected_fcf": "FCF_t = starting_fcf * (1 + annual_fcf_growth) ^ t",
    "present_value": "PV(FCF_t) = FCF_t / (1 + discount_rate) ^ t",
    "terminal_value": "TV_n = FCF_n * (1 + terminal_growth) / (discount_rate - terminal_growth)",
    "enterprise_value": "EV = sum(PV(FCF_1..n)) + TV_n / (1 + discount_rate) ^ n",
    "equity_value": "equity_value = enterprise_value - net_debt",
    "diluted_shares": "shares_n = shares_outstanding * (1 + annual_share_dilution) ^ n",
    "intrinsic_value_per_share": "intrinsic_value_per_share = equity_value / shares_n",
    "margin_of_safety": "margin_of_safety = 1 - current_price / intrinsic_value_per_share",
    "expected_annual_return": "((intrinsic_value_per_share + cumulative_dividends_per_share) / current_price) ^ (1 / forecast_years) - 1",
}

EARNINGS_EXIT_FORMULAS = {
    "projected_earnings": "earnings_t = starting_earnings * (1 + annual_earnings_growth) ^ t",
    "terminal_equity_value": "terminal_equity_value = earnings_n * earnings_exit_multiple",
    "diluted_shares": "shares_n = shares_outstanding * (1 + annual_share_dilution) ^ n",
    "terminal_value_per_share": "terminal_value_per_share = terminal_equity_value / shares_n",
    "intrinsic_value_per_share": "intrinsic_value_per_share = terminal_value_per_share / (1 + discount_rate) ^ n",
    "margin_of_safety": "margin_of_safety = 1 - current_price / intrinsic_value_per_share",
    "expected_annual_return": "((terminal_value_per_share + cumulative_dividends_per_share) / current_price) ^ (1 / forecast_years) - 1",
}


class ValuationEngine:
    """Calculate downside/base/upside values without an LLM dependency."""

    def calculate(self, raw: ValuationRequest | Mapping[str, object]) -> ValuationReport:
        request = raw if isinstance(raw, ValuationRequest) else ValuationRequest.from_dict(raw)
        errors = self._validate(request)
        if errors:
            return self._invalid_report(request, errors)

        scenario_values: dict[ScenarioName, ScenarioValuation] = {}
        with localcontext() as context:
            context.prec = 36
            for assumptions in request.scenarios:
                if request.method is ValuationMethod.FCF_DCF:
                    value = self._calculate_dcf_scenario(request, assumptions)
                else:
                    value = self._calculate_earnings_scenario(request, assumptions)
                scenario_values[assumptions.name] = value

        downside = scenario_values[ScenarioName.DOWNSIDE].intrinsic_value_per_share
        base = scenario_values[ScenarioName.BASE].intrinsic_value_per_share
        upside = scenario_values[ScenarioName.UPSIDE].intrinsic_value_per_share
        if not downside <= base <= upside:
            errors.append(
                "scenario_order_invalid: intrinsic values must satisfy downside <= base <= upside"
            )
        if base <= 0:
            errors.append("base_intrinsic_value_non_positive")

        cross_validation = self._cross_validate(request, base)
        price_bands = None
        if base > 0:
            price_bands = PriceBands(
                watch_price=downside,
                entry_price_ceiling=base * (Decimal("1") - request.required_margin_of_safety),
                fair_value_low=downside,
                fair_value_high=upside,
                expensive_price=base * (Decimal("1") + request.overvaluation_premium),
            )

        return ValuationReport(
            security_id=request.security_id,
            as_of=request.as_of,
            currency=request.currency,
            current_price=request.current_price,
            forecast_years=request.forecast_years,
            valid=not errors,
            errors=tuple(errors),
            scenarios=scenario_values,
            price_bands=price_bands,
            cross_validation=cross_validation,
            formula_version=(
                VALUATION_FORMULA_VERSION
                if request.method is ValuationMethod.FCF_DCF
                else EARNINGS_VALUATION_FORMULA_VERSION
            ),
            input_evidence_ids=request.evidence_ids(),
        )

    @staticmethod
    def _validate(request: ValuationRequest) -> list[str]:
        errors: list[str] = []
        if not request.security_id:
            errors.append("security_id_missing")
        if request.current_price is None or request.current_price <= 0:
            errors.append("current_price_must_be_positive")
        if request.method is ValuationMethod.FCF_DCF and (
            request.starting_fcf is None or request.starting_fcf <= 0
        ):
            errors.append("starting_fcf_must_be_positive_for_fcf_dcf")
        if request.method is ValuationMethod.EARNINGS_EXIT_MULTIPLE and (
            request.starting_earnings is None or request.starting_earnings <= 0
        ):
            errors.append("starting_earnings_must_be_positive_for_earnings_exit_multiple")
        if request.shares_outstanding is None or request.shares_outstanding <= 0:
            errors.append("shares_outstanding_must_be_positive")
        if request.forecast_years <= 0 or request.forecast_years > 30:
            errors.append("forecast_years_must_be_between_1_and_30")
        if not Decimal("0") <= request.required_margin_of_safety < Decimal("1"):
            errors.append("required_margin_of_safety_must_be_in_[0,1)")
        if request.overvaluation_premium < 0:
            errors.append("overvaluation_premium_must_be_non_negative")
        if request.cross_check_tolerance < 0:
            errors.append("cross_check_tolerance_must_be_non_negative")

        names = [scenario.name for scenario in request.scenarios]
        expected_names = {ScenarioName.DOWNSIDE, ScenarioName.BASE, ScenarioName.UPSIDE}
        if len(names) != 3 or set(names) != expected_names:
            errors.append("exactly_one_downside_base_and_upside_scenario_required")
        for scenario in request.scenarios:
            prefix = scenario.name.value
            if scenario.discount_rate is None or scenario.discount_rate <= Decimal("0"):
                errors.append(f"{prefix}.discount_rate_must_be_positive")
            if request.method is ValuationMethod.FCF_DCF:
                if scenario.annual_fcf_growth is None or scenario.annual_fcf_growth <= Decimal("-1"):
                    errors.append(f"{prefix}.annual_fcf_growth_must_exceed_-1")
                if scenario.terminal_growth is None or scenario.terminal_growth <= Decimal("-1"):
                    errors.append(f"{prefix}.terminal_growth_must_exceed_-1")
                if (
                    scenario.discount_rate is not None
                    and scenario.terminal_growth is not None
                    and scenario.discount_rate <= scenario.terminal_growth
                ):
                    errors.append(f"{prefix}.discount_rate_must_exceed_terminal_growth")
            else:
                if (
                    scenario.annual_earnings_growth is None
                    or scenario.annual_earnings_growth <= Decimal("-1")
                ):
                    errors.append(f"{prefix}.annual_earnings_growth_must_exceed_-1")
                if (
                    scenario.earnings_exit_multiple is None
                    or scenario.earnings_exit_multiple <= Decimal("0")
                ):
                    errors.append(f"{prefix}.earnings_exit_multiple_must_be_positive")
            if scenario.annual_share_dilution is None or scenario.annual_share_dilution <= Decimal("-1"):
                errors.append(f"{prefix}.annual_share_dilution_must_exceed_-1")
        for check in request.cross_checks:
            if check.value_per_share <= 0:
                errors.append(f"cross_check.{check.method}.value_per_share_must_be_positive")
        return errors

    @staticmethod
    def _calculate_dcf_scenario(
        request: ValuationRequest, assumptions: ScenarioAssumptions
    ) -> ScenarioValuation:
        assert request.starting_fcf is not None
        assert assumptions.annual_fcf_growth is not None
        assert assumptions.discount_rate is not None
        assert assumptions.terminal_growth is not None
        assert assumptions.annual_share_dilution is not None
        projected_fcfs: list[Decimal] = []
        present_values: list[Decimal] = []
        for year in range(1, request.forecast_years + 1):
            fcf = request.starting_fcf * (Decimal("1") + assumptions.annual_fcf_growth) ** year
            present_value = fcf / (Decimal("1") + assumptions.discount_rate) ** year
            projected_fcfs.append(fcf)
            present_values.append(present_value)

        last_fcf = projected_fcfs[-1]
        terminal_value = last_fcf * (Decimal("1") + assumptions.terminal_growth) / (
            assumptions.discount_rate - assumptions.terminal_growth
        )
        present_value_of_terminal = terminal_value / (
            Decimal("1") + assumptions.discount_rate
        ) ** request.forecast_years
        enterprise_value = sum(present_values, Decimal("0")) + present_value_of_terminal
        equity_value = enterprise_value - request.net_debt
        diluted_shares = request.shares_outstanding * (
            Decimal("1") + assumptions.annual_share_dilution
        ) ** request.forecast_years
        intrinsic_value = equity_value / diluted_shares
        margin = (
            Decimal("1") - request.current_price / intrinsic_value
            if intrinsic_value > 0
            else None
        )
        return_numerator = intrinsic_value + request.cumulative_dividends_per_share
        expected_return: Decimal | None = None
        if return_numerator > 0:
            expected_return = (return_numerator / request.current_price) ** (
                Decimal("1") / Decimal(request.forecast_years)
            ) - Decimal("1")

        evidence_ids = tuple(
            dict.fromkeys(request.evidence_ids() + assumptions.evidence_ids)
        )
        return ScenarioValuation(
            name=assumptions.name,
            valuation_method=ValuationMethod.FCF_DCF,
            enterprise_value=enterprise_value,
            equity_value=equity_value,
            diluted_shares_at_horizon=diluted_shares,
            intrinsic_value_per_share=intrinsic_value,
            margin_of_safety=margin,
            expected_annual_return=expected_return,
            projected_fcfs=tuple(projected_fcfs),
            projected_earnings=(),
            present_values=tuple(present_values),
            terminal_value=terminal_value,
            present_value_of_terminal=present_value_of_terminal,
            terminal_value_per_share=intrinsic_value,
            assumptions=assumptions,
            formulas=DCF_FORMULAS,
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _calculate_earnings_scenario(
        request: ValuationRequest, assumptions: ScenarioAssumptions
    ) -> ScenarioValuation:
        assert request.starting_earnings is not None
        assert assumptions.annual_earnings_growth is not None
        assert assumptions.earnings_exit_multiple is not None
        assert assumptions.discount_rate is not None
        assert assumptions.annual_share_dilution is not None
        projected_earnings = tuple(
            request.starting_earnings
            * (Decimal("1") + assumptions.annual_earnings_growth) ** year
            for year in range(1, request.forecast_years + 1)
        )
        terminal_equity_value = projected_earnings[-1] * assumptions.earnings_exit_multiple
        diluted_shares = request.shares_outstanding * (
            Decimal("1") + assumptions.annual_share_dilution
        ) ** request.forecast_years
        terminal_value_per_share = terminal_equity_value / diluted_shares
        discount_factor = (Decimal("1") + assumptions.discount_rate) ** request.forecast_years
        intrinsic_value = terminal_value_per_share / discount_factor
        present_equity_value = terminal_equity_value / discount_factor
        implied_enterprise_value = present_equity_value + request.net_debt
        margin = Decimal("1") - request.current_price / intrinsic_value if intrinsic_value > 0 else None
        return_numerator = terminal_value_per_share + request.cumulative_dividends_per_share
        expected_return = (return_numerator / request.current_price) ** (
            Decimal("1") / Decimal(request.forecast_years)
        ) - Decimal("1")
        evidence_ids = tuple(dict.fromkeys(request.evidence_ids() + assumptions.evidence_ids))
        return ScenarioValuation(
            name=assumptions.name,
            valuation_method=ValuationMethod.EARNINGS_EXIT_MULTIPLE,
            enterprise_value=implied_enterprise_value,
            equity_value=present_equity_value,
            diluted_shares_at_horizon=diluted_shares,
            intrinsic_value_per_share=intrinsic_value,
            margin_of_safety=margin,
            expected_annual_return=expected_return,
            projected_fcfs=(),
            projected_earnings=projected_earnings,
            present_values=(),
            terminal_value=terminal_equity_value,
            present_value_of_terminal=present_equity_value,
            terminal_value_per_share=terminal_value_per_share,
            assumptions=assumptions,
            formulas=EARNINGS_EXIT_FORMULAS,
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _cross_validate(request: ValuationRequest, base_value: Decimal) -> CrossValidationResult:
        if not request.cross_checks:
            return CrossValidationResult(
                available=False,
                passed=False,
                reference_value_per_share=None,
                relative_gap=None,
                tolerance=request.cross_check_tolerance,
                methods=(),
                evidence_ids=(),
                explanation="no independent valuation cross-check was supplied",
            )
        sorted_values = sorted(check.value_per_share for check in request.cross_checks)
        count = len(sorted_values)
        midpoint = count // 2
        if count % 2:
            reference = sorted_values[midpoint]
        else:
            reference = (sorted_values[midpoint - 1] + sorted_values[midpoint]) / Decimal("2")
        gap = abs(reference - base_value) / abs(base_value) if base_value != 0 else None
        passed = gap is not None and gap <= request.cross_check_tolerance
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for check in request.cross_checks
                for evidence_id in check.evidence_ids
            )
        )
        return CrossValidationResult(
            available=True,
            passed=passed,
            reference_value_per_share=reference,
            relative_gap=gap,
            tolerance=request.cross_check_tolerance,
            methods=tuple(check.method for check in request.cross_checks),
            evidence_ids=evidence_ids,
            explanation=(
                "independent cross-check is within tolerance"
                if passed
                else "independent cross-check differs from base DCF beyond tolerance"
            ),
        )

    @staticmethod
    def _invalid_report(request: ValuationRequest, errors: list[str]) -> ValuationReport:
        return ValuationReport(
            security_id=request.security_id,
            as_of=request.as_of,
            currency=request.currency,
            current_price=request.current_price,
            forecast_years=request.forecast_years,
            valid=False,
            errors=tuple(errors),
            scenarios={},
            price_bands=None,
            cross_validation=CrossValidationResult(
                available=False,
                passed=False,
                reference_value_per_share=None,
                relative_gap=None,
                tolerance=request.cross_check_tolerance,
                methods=(),
                evidence_ids=(),
                explanation="cross-validation was not run because the DCF input was invalid",
            ),
            formula_version=(
                VALUATION_FORMULA_VERSION
                if request.method is ValuationMethod.FCF_DCF
                else EARNINGS_VALUATION_FORMULA_VERSION
            ),
            input_evidence_ids=request.evidence_ids(),
        )
