import unittest
from decimal import Decimal

from stock_agent.metrics import FinancialSnapshot, MetricEngine, MetricStatus, cagr, growth_rate


def complete_snapshot(**overrides):
    payload = {
        "period_end": "2025-12-31",
        "currency": "CNY",
        "revenue": "1000",
        "gross_profit": "600",
        "ebit": "300",
        "ebitda": "350",
        "net_income": "220",
        "cfo": "280",
        "capex": "50",
        "capitalized_software": "10",
        "total_assets_begin": "1800",
        "total_assets_end": "2200",
        "invested_capital_begin": "800",
        "invested_capital_end": "1000",
        "equity_begin": "1200",
        "equity_end": "1400",
        "normalized_tax_rate": "0.20",
        "debt": "400",
        "cash_and_short_term_investments": "500",
        "interest_expense": "30",
        "diluted_shares": "100",
        "market_price": "15",
        "evidence": {
            "revenue": ["fact-revenue"],
            "ebit": ["fact-ebit"],
            "normalized_tax_rate": ["policy-tax"],
            "invested_capital_begin": ["fact-ic-begin"],
            "invested_capital_end": ["fact-ic-end"],
        },
    }
    payload.update(overrides)
    return FinancialSnapshot.from_dict(payload)


class MetricEngineTests(unittest.TestCase):
    def test_computes_quality_cash_flow_leverage_and_valuation_metrics(self):
        report = MetricEngine().compute(complete_snapshot())
        self.assertEqual(report.get("gross_margin").value, Decimal("0.6"))
        self.assertEqual(report.get("nopat").value, Decimal("240.00"))
        self.assertEqual(report.get("roic").value, Decimal("240.00") / Decimal("900"))
        self.assertEqual(report.get("free_cash_flow").value, Decimal("220"))
        self.assertEqual(report.get("cash_conversion").value, Decimal("280") / Decimal("220"))
        self.assertEqual(report.get("accrual_ratio").value, Decimal("-60") / Decimal("2000"))
        self.assertEqual(report.get("net_debt").value, Decimal("-100"))
        self.assertEqual(report.get("enterprise_value").value, Decimal("1400"))
        self.assertEqual(report.get("fcf_yield").value, Decimal("220") / Decimal("1500"))
        self.assertEqual(
            report.get("roic").evidence_ids,
            ("fact-ebit", "policy-tax", "fact-ic-begin", "fact-ic-end"),
        )

    def test_bad_denominators_are_not_reported_as_misleading_multiples(self):
        report = MetricEngine().compute(
            complete_snapshot(net_income="-1", ebitda="0", interest_expense="0")
        )
        self.assertIsNone(report.get("price_to_earnings").value)
        self.assertIs(report.get("price_to_earnings").status, MetricStatus.NOT_MEANINGFUL)
        self.assertIs(report.get("net_debt_to_ebitda").status, MetricStatus.NOT_MEANINGFUL)
        self.assertIs(report.get("interest_coverage").status, MetricStatus.NOT_MEANINGFUL)

    def test_missing_inputs_and_decimal_serialization_are_explicit(self):
        report = MetricEngine().compute(
            {"period_end": "2025-12-31", "currency": "HKD", "revenue": "100"}
        )
        self.assertIs(report.get("free_cash_flow").status, MetricStatus.MISSING)
        payload = report.to_dict()
        self.assertIsNone(payload["metrics"]["gross_margin"]["value"])
        self.assertEqual(payload["metrics"]["gross_margin"]["inputs"]["revenue"], "100")

    def test_growth_helpers_fail_closed_on_bad_denominators(self):
        self.assertEqual(growth_rate("120", "100"), Decimal("0.2"))
        self.assertIsNone(growth_rate("120", "0"))
        self.assertEqual(cagr("121", "100", 2), Decimal("0.1"))
        self.assertIsNone(cagr("121", "100", 0))

    def test_snapshot_rejects_non_finite_numbers_and_invalid_tax_rate(self):
        with self.assertRaises(ValueError):
            complete_snapshot(revenue="NaN")
        with self.assertRaises(ValueError):
            complete_snapshot(normalized_tax_rate="1.01")


if __name__ == "__main__":
    unittest.main()
