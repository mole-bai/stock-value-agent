"""Deterministic financial metric calculations for ordinary companies."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Iterable, Mapping

from .models import FinancialSnapshot, MetricResult, MetricsReport, MetricStatus, to_decimal


FORMULA_REGISTRY_VERSION = "metrics.non_financial.v1"
EPSILON = Decimal("0.000000000001")


def _average(first: Decimal | None, second: Decimal | None) -> Decimal | None:
    if first is None or second is None:
        return None
    return (first + second) / Decimal("2")


def _metric(
    snapshot: FinancialSnapshot,
    *,
    name: str,
    value: Decimal | None,
    unit: str,
    status: MetricStatus,
    formula: str,
    inputs: Mapping[str, Decimal | None],
    evidence_fields: Iterable[str],
    explanation: str | None = None,
) -> MetricResult:
    return MetricResult(
        name=name,
        value=value,
        unit=unit,
        status=status,
        formula=formula,
        formula_version=FORMULA_REGISTRY_VERSION,
        inputs=inputs,
        evidence_ids=snapshot.evidence_for(*tuple(evidence_fields)),
        explanation=explanation,
    )


def _ratio(
    snapshot: FinancialSnapshot,
    *,
    name: str,
    numerator: Decimal | None,
    denominator: Decimal | None,
    numerator_name: str,
    denominator_name: str,
    formula: str,
    evidence_fields: Iterable[str],
    require_positive_denominator: bool = True,
) -> MetricResult:
    inputs = {numerator_name: numerator, denominator_name: denominator}
    if numerator is None or denominator is None:
        return _metric(
            snapshot,
            name=name,
            value=None,
            unit="ratio",
            status=MetricStatus.MISSING,
            formula=formula,
            inputs=inputs,
            evidence_fields=evidence_fields,
            explanation="required input is missing",
        )
    invalid_denominator = (
        denominator <= EPSILON if require_positive_denominator else abs(denominator) <= EPSILON
    )
    if invalid_denominator:
        return _metric(
            snapshot,
            name=name,
            value=None,
            unit="ratio",
            status=MetricStatus.NOT_MEANINGFUL,
            formula=formula,
            inputs=inputs,
            evidence_fields=evidence_fields,
            explanation="denominator is non-positive or too close to zero",
        )
    return _metric(
        snapshot,
        name=name,
        value=numerator / denominator,
        unit="ratio",
        status=MetricStatus.OK,
        formula=formula,
        inputs=inputs,
        evidence_fields=evidence_fields,
    )


class MetricEngine:
    """Compute a fixed, versioned set of value-investing metrics."""

    def compute(self, raw: FinancialSnapshot | Mapping[str, object]) -> MetricsReport:
        snapshot = raw if isinstance(raw, FinancialSnapshot) else FinancialSnapshot.from_dict(raw)
        metrics: dict[str, MetricResult] = {}

        metrics["gross_margin"] = _ratio(
            snapshot,
            name="gross_margin",
            numerator=snapshot.gross_profit,
            denominator=snapshot.revenue,
            numerator_name="gross_profit",
            denominator_name="revenue",
            formula="gross_profit / revenue",
            evidence_fields=("gross_profit", "revenue"),
        )
        metrics["operating_margin"] = _ratio(
            snapshot,
            name="operating_margin",
            numerator=snapshot.ebit,
            denominator=snapshot.revenue,
            numerator_name="ebit",
            denominator_name="revenue",
            formula="ebit / revenue",
            evidence_fields=("ebit", "revenue"),
        )
        metrics["net_margin"] = _ratio(
            snapshot,
            name="net_margin",
            numerator=snapshot.net_income,
            denominator=snapshot.revenue,
            numerator_name="net_income",
            denominator_name="revenue",
            formula="net_income / revenue",
            evidence_fields=("net_income", "revenue"),
        )

        nopat: Decimal | None = None
        if snapshot.ebit is not None and snapshot.normalized_tax_rate is not None:
            nopat = snapshot.ebit * (Decimal("1") - snapshot.normalized_tax_rate)
        metrics["nopat"] = _metric(
            snapshot,
            name="nopat",
            value=nopat,
            unit=f"{snapshot.currency} x {snapshot.scale}",
            status=MetricStatus.OK if nopat is not None else MetricStatus.MISSING,
            formula="ebit * (1 - normalized_tax_rate)",
            inputs={"ebit": snapshot.ebit, "normalized_tax_rate": snapshot.normalized_tax_rate},
            evidence_fields=("ebit", "normalized_tax_rate"),
            explanation=None if nopat is not None else "ebit or normalized tax rate is missing",
        )

        average_invested_capital = _average(
            snapshot.invested_capital_begin, snapshot.invested_capital_end
        )
        metrics["roic"] = _ratio(
            snapshot,
            name="roic",
            numerator=nopat,
            denominator=average_invested_capital,
            numerator_name="nopat",
            denominator_name="average_invested_capital",
            formula="ebit * (1 - normalized_tax_rate) / ((invested_capital_begin + invested_capital_end) / 2)",
            evidence_fields=(
                "ebit",
                "normalized_tax_rate",
                "invested_capital_begin",
                "invested_capital_end",
            ),
        )

        average_equity = _average(snapshot.equity_begin, snapshot.equity_end)
        metrics["roe"] = _ratio(
            snapshot,
            name="roe",
            numerator=snapshot.net_income,
            denominator=average_equity,
            numerator_name="net_income",
            denominator_name="average_equity",
            formula="net_income / ((equity_begin + equity_end) / 2)",
            evidence_fields=("net_income", "equity_begin", "equity_end"),
        )

        metrics["cash_conversion"] = _ratio(
            snapshot,
            name="cash_conversion",
            numerator=snapshot.cfo,
            denominator=snapshot.net_income,
            numerator_name="cfo",
            denominator_name="net_income",
            formula="cfo / net_income",
            evidence_fields=("cfo", "net_income"),
        )

        average_assets = _average(snapshot.total_assets_begin, snapshot.total_assets_end)
        accrual_numerator = (
            snapshot.net_income - snapshot.cfo
            if snapshot.net_income is not None and snapshot.cfo is not None
            else None
        )
        metrics["accrual_ratio"] = _ratio(
            snapshot,
            name="accrual_ratio",
            numerator=accrual_numerator,
            denominator=average_assets,
            numerator_name="net_income_minus_cfo",
            denominator_name="average_total_assets",
            formula="(net_income - cfo) / ((total_assets_begin + total_assets_end) / 2)",
            evidence_fields=("net_income", "cfo", "total_assets_begin", "total_assets_end"),
        )

        fcf: Decimal | None = None
        if snapshot.cfo is not None and snapshot.capex is not None:
            fcf = snapshot.cfo - snapshot.capex - snapshot.capitalized_software
        metrics["free_cash_flow"] = _metric(
            snapshot,
            name="free_cash_flow",
            value=fcf,
            unit=f"{snapshot.currency} x {snapshot.scale}",
            status=MetricStatus.OK if fcf is not None else MetricStatus.MISSING,
            formula="cfo - capex - capitalized_software",
            inputs={
                "cfo": snapshot.cfo,
                "capex": snapshot.capex,
                "capitalized_software": snapshot.capitalized_software,
            },
            evidence_fields=("cfo", "capex", "capitalized_software"),
            explanation=None if fcf is not None else "cfo or capex is missing",
        )
        metrics["fcf_margin"] = _ratio(
            snapshot,
            name="fcf_margin",
            numerator=fcf,
            denominator=snapshot.revenue,
            numerator_name="free_cash_flow",
            denominator_name="revenue",
            formula="(cfo - capex - capitalized_software) / revenue",
            evidence_fields=("cfo", "capex", "capitalized_software", "revenue"),
        )

        net_debt: Decimal | None = None
        if snapshot.debt is not None and snapshot.cash_and_short_term_investments is not None:
            net_debt = snapshot.debt - snapshot.cash_and_short_term_investments
        metrics["net_debt"] = _metric(
            snapshot,
            name="net_debt",
            value=net_debt,
            unit=f"{snapshot.currency} x {snapshot.scale}",
            status=MetricStatus.OK if net_debt is not None else MetricStatus.MISSING,
            formula="debt - cash_and_short_term_investments",
            inputs={
                "debt": snapshot.debt,
                "cash_and_short_term_investments": snapshot.cash_and_short_term_investments,
            },
            evidence_fields=("debt", "cash_and_short_term_investments"),
            explanation=None if net_debt is not None else "debt or cash is missing",
        )
        metrics["net_debt_to_ebitda"] = _ratio(
            snapshot,
            name="net_debt_to_ebitda",
            numerator=net_debt,
            denominator=snapshot.ebitda,
            numerator_name="net_debt",
            denominator_name="ebitda",
            formula="(debt - cash_and_short_term_investments) / ebitda",
            evidence_fields=("debt", "cash_and_short_term_investments", "ebitda"),
        )
        metrics["interest_coverage"] = _ratio(
            snapshot,
            name="interest_coverage",
            numerator=snapshot.ebit,
            denominator=snapshot.interest_expense,
            numerator_name="ebit",
            denominator_name="interest_expense",
            formula="ebit / interest_expense",
            evidence_fields=("ebit", "interest_expense"),
        )

        market_cap: Decimal | None = None
        if snapshot.market_price is not None and snapshot.diluted_shares is not None:
            market_cap = snapshot.market_price * snapshot.diluted_shares
        metrics["market_cap"] = _metric(
            snapshot,
            name="market_cap",
            value=market_cap,
            unit=f"{snapshot.currency} x {snapshot.scale}",
            status=MetricStatus.OK if market_cap is not None else MetricStatus.MISSING,
            formula="market_price * diluted_shares",
            inputs={"market_price": snapshot.market_price, "diluted_shares": snapshot.diluted_shares},
            evidence_fields=("market_price", "diluted_shares"),
            explanation=None if market_cap is not None else "price or diluted shares is missing",
        )

        enterprise_value: Decimal | None = None
        if (
            market_cap is not None
            and snapshot.debt is not None
            and snapshot.cash_and_short_term_investments is not None
        ):
            enterprise_value = (
                market_cap
                + snapshot.debt
                + snapshot.preferred_stock
                + snapshot.minority_interest
                - snapshot.cash_and_short_term_investments
            )
        metrics["enterprise_value"] = _metric(
            snapshot,
            name="enterprise_value",
            value=enterprise_value,
            unit=f"{snapshot.currency} x {snapshot.scale}",
            status=MetricStatus.OK if enterprise_value is not None else MetricStatus.MISSING,
            formula="market_cap + debt + preferred_stock + minority_interest - cash_and_short_term_investments",
            inputs={
                "market_cap": market_cap,
                "debt": snapshot.debt,
                "preferred_stock": snapshot.preferred_stock,
                "minority_interest": snapshot.minority_interest,
                "cash_and_short_term_investments": snapshot.cash_and_short_term_investments,
            },
            evidence_fields=(
                "market_price",
                "diluted_shares",
                "debt",
                "preferred_stock",
                "minority_interest",
                "cash_and_short_term_investments",
            ),
            explanation=None if enterprise_value is not None else "market cap, debt or cash is missing",
        )
        metrics["price_to_earnings"] = _ratio(
            snapshot,
            name="price_to_earnings",
            numerator=market_cap,
            denominator=snapshot.net_income,
            numerator_name="market_cap",
            denominator_name="net_income",
            formula="market_cap / net_income",
            evidence_fields=("market_price", "diluted_shares", "net_income"),
        )
        metrics["ev_to_ebit"] = _ratio(
            snapshot,
            name="ev_to_ebit",
            numerator=enterprise_value,
            denominator=snapshot.ebit,
            numerator_name="enterprise_value",
            denominator_name="ebit",
            formula="enterprise_value / ebit",
            evidence_fields=(
                "market_price",
                "diluted_shares",
                "debt",
                "cash_and_short_term_investments",
                "ebit",
            ),
        )
        metrics["ev_to_ebitda"] = _ratio(
            snapshot,
            name="ev_to_ebitda",
            numerator=enterprise_value,
            denominator=snapshot.ebitda,
            numerator_name="enterprise_value",
            denominator_name="ebitda",
            formula="enterprise_value / ebitda",
            evidence_fields=(
                "market_price",
                "diluted_shares",
                "debt",
                "cash_and_short_term_investments",
                "ebitda",
            ),
        )
        metrics["fcf_yield"] = _ratio(
            snapshot,
            name="fcf_yield",
            numerator=fcf,
            denominator=market_cap,
            numerator_name="free_cash_flow",
            denominator_name="market_cap",
            formula="(cfo - capex - capitalized_software) / market_cap",
            evidence_fields=(
                "cfo",
                "capex",
                "capitalized_software",
                "market_price",
                "diluted_shares",
            ),
        )

        return MetricsReport(
            period_end=snapshot.period_end,
            currency=snapshot.currency,
            formula_registry_version=FORMULA_REGISTRY_VERSION,
            metrics=metrics,
        )


def growth_rate(current: object, previous: object) -> Decimal | None:
    """Return period-on-period growth, or ``None`` for a bad denominator."""

    current_decimal = to_decimal(current, field_name="current")
    previous_decimal = to_decimal(previous, field_name="previous")
    if current_decimal is None or previous_decimal is None or previous_decimal <= EPSILON:
        return None
    return current_decimal / previous_decimal - Decimal("1")


def cagr(ending: object, beginning: object, years: int) -> Decimal | None:
    """Return CAGR using Decimal arithmetic and a high precision context."""

    ending_decimal = to_decimal(ending, field_name="ending")
    beginning_decimal = to_decimal(beginning, field_name="beginning")
    if (
        ending_decimal is None
        or beginning_decimal is None
        or ending_decimal <= 0
        or beginning_decimal <= 0
        or years <= 0
    ):
        return None
    with localcontext() as context:
        context.prec = 36
        return (ending_decimal / beginning_decimal) ** (Decimal("1") / Decimal(years)) - Decimal(
            "1"
        )
