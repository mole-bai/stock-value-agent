from __future__ import annotations

import unittest
from decimal import Decimal

from stock_agent.assessment import ASSESSMENT_VERSION, AssessmentEngine
from stock_agent.valuation import ValuationEngine


def valuation():
    return ValuationEngine().calculate(
        {
            "security_id": "TEST",
            "as_of": "2026-08-01",
            "currency": "CNY",
            "current_price": "60",
            "starting_fcf": "100",
            "shares_outstanding": "10",
            "forecast_years": 5,
            "scenarios": [
                {
                    "name": "downside",
                    "annual_fcf_growth": "0",
                    "discount_rate": "0.12",
                    "terminal_growth": "0.01",
                },
                {
                    "name": "base",
                    "annual_fcf_growth": "0.05",
                    "discount_rate": "0.10",
                    "terminal_growth": "0.02",
                },
                {
                    "name": "upside",
                    "annual_fcf_growth": "0.10",
                    "discount_rate": "0.09",
                    "terminal_growth": "0.03",
                },
            ],
        }
    )


def financial(*, missing_cashflow=False, watch_signal=False):
    facts = {
        "profit": "0.8",
        "growth": "0.6",
        "cash": "0.7",
        "debt_ratio": "0.2",
        "payout": "0.5",
    }
    if missing_cashflow:
        facts.pop("cash")
    factors = [
        {
            "factor_id": "profit",
            "label": "Profit",
            "dimension": "profitability",
            "fact": "profit",
            "bad": "0",
            "good": "1",
        },
        {
            "factor_id": "growth",
            "label": "Growth",
            "dimension": "growth",
            "fact": "growth",
            "bad": "0",
            "good": "1",
        },
        {
            "factor_id": "cash",
            "label": "Cash",
            "dimension": "cashflow",
            "fact": "cash",
            "bad": "0",
            "good": "1",
        },
        {
            "factor_id": "debt",
            "label": "Debt",
            "dimension": "balance_sheet",
            "fact": "debt_ratio",
            "bad": "1",
            "good": "0",
        },
        {
            "factor_id": "payout",
            "label": "Payout",
            "dimension": "capital_allocation",
            "fact": "payout",
            "bad": "0",
            "good": "1",
        },
    ]
    return {
        "confidence": "medium",
        "facts": facts,
        "signals": ([{"severity": "watch"}] if watch_signal else []),
        "red_flags": [],
        "valuation": {
            "margin_of_safety": "0.25",
            "minimum_buy_return": "0.12",
        },
        "assessment": {
            "minimum_quality_score": "60",
            "minimum_buy_score": "70",
            "minimum_coverage": "0.65",
            "factors": factors,
        },
    }


class AssessmentEngineTests(unittest.TestCase):
    def test_builds_explainable_score_and_handles_lower_is_better(self):
        result = AssessmentEngine().compute(financial(), valuation())

        self.assertEqual(result.version, ASSESSMENT_VERSION)
        balance = next(
            item for item in result.dimensions if item.dimension == "balance_sheet"
        )
        self.assertEqual(balance.score, Decimal("80.0"))
        self.assertTrue(result.quality_qualified)
        self.assertGreater(result.adjusted_margin_of_safety, Decimal("0.25"))
        self.assertGreater(result.adjusted_target_return, Decimal("0.12"))
        self.assertEqual(len(result.dimensions), 6)

    def test_missing_factor_is_neutral_but_reduces_coverage(self):
        complete = AssessmentEngine().compute(financial(), valuation())
        missing = AssessmentEngine().compute(
            financial(missing_cashflow=True), valuation()
        )

        self.assertLess(missing.quality_coverage, complete.quality_coverage)
        cashflow = next(
            item for item in missing.dimensions if item.dimension == "cashflow"
        )
        self.assertIsNone(cashflow.score)
        self.assertEqual(cashflow.coverage, Decimal("0.000"))

    def test_watch_signals_apply_visible_risk_penalty(self):
        plain = AssessmentEngine().compute(financial(), valuation())
        warned = AssessmentEngine().compute(
            financial(watch_signal=True), valuation()
        )

        self.assertEqual(warned.risk_penalty, Decimal("2"))
        self.assertEqual(warned.composite_score, plain.composite_score - Decimal("2.0"))


if __name__ == "__main__":
    unittest.main()
