import unittest
from decimal import Decimal, localcontext

from stock_agent.valuation import ScenarioName, ValuationEngine, ValuationMethod, ValuationReport


def valuation_payload(**overrides):
    payload = {
        "security_id": "0700.HK",
        "as_of": "2026-07-31",
        "currency": "HKD",
        "current_price": "100",
        "starting_fcf": "100",
        "shares_outstanding": "10",
        "net_debt": "0",
        "forecast_years": 5,
        "cumulative_dividends_per_share": "5",
        "required_margin_of_safety": "0.25",
        "scenarios": [
            {"name": "bear", "annual_fcf_growth": "0", "discount_rate": "0.12", "terminal_growth": "0.01", "annual_share_dilution": "0.02", "evidence_ids": ["assumption-bear"]},
            {"name": "base", "annual_fcf_growth": "0.05", "discount_rate": "0.10", "terminal_growth": "0.02", "annual_share_dilution": "0.01", "evidence_ids": ["assumption-base"]},
            {"name": "bull", "annual_fcf_growth": "0.10", "discount_rate": "0.09", "terminal_growth": "0.03", "annual_share_dilution": "0", "evidence_ids": ["assumption-bull"]},
        ],
        "cross_checks": [
            {"method": "normalized_pe", "value_per_share": "130", "evidence_ids": ["peer-1"]},
            {"method": "fcf_yield", "value_per_share": "140", "evidence_ids": ["peer-2"]},
        ],
        "cross_check_tolerance": "0.25",
        "evidence": {"starting_fcf": ["fact-fcf"], "shares_outstanding": ["fact-shares"], "net_debt": ["fact-net-debt"], "current_price": ["price-close"]},
    }
    payload.update(overrides)
    return payload


class ValuationEngineTests(unittest.TestCase):
    def test_three_scenario_dcf_is_ordered_auditable_and_round_trips(self):
        report = ValuationEngine().calculate(valuation_payload())
        self.assertTrue(report.valid)
        self.assertEqual(set(report.scenarios), {ScenarioName.DOWNSIDE, ScenarioName.BASE, ScenarioName.UPSIDE})
        downside, base, upside = report.scenarios[ScenarioName.DOWNSIDE], report.base, report.scenarios[ScenarioName.UPSIDE]
        self.assertIsNotNone(base)
        self.assertLess(downside.intrinsic_value_per_share, base.intrinsic_value_per_share)
        self.assertLess(base.intrinsic_value_per_share, upside.intrinsic_value_per_share)
        self.assertEqual(base.projected_fcfs[0], Decimal("105.00"))
        with localcontext() as context:
            context.prec = 36
            self.assertEqual(base.margin_of_safety, Decimal("1") - Decimal("100") / base.intrinsic_value_per_share)
        restored = ValuationReport.from_dict(report.to_dict())
        self.assertEqual(restored.base.intrinsic_value_per_share, base.intrinsic_value_per_share)

    def test_cross_validation_uses_median_reference(self):
        report = ValuationEngine().calculate(valuation_payload())
        self.assertTrue(report.cross_validation.available)
        self.assertEqual(report.cross_validation.reference_value_per_share, Decimal("135"))
        self.assertEqual(report.cross_validation.evidence_ids, ("peer-1", "peer-2"))

    def test_invalid_rates_and_unstable_fcf_fail_without_fake_values(self):
        scenarios = valuation_payload()["scenarios"]
        scenarios[1] = {**scenarios[1], "discount_rate": "0.02", "terminal_growth": "0.02"}
        report = ValuationEngine().calculate(valuation_payload(starting_fcf="-1", scenarios=scenarios))
        self.assertFalse(report.valid)
        self.assertEqual(report.scenarios, {})
        self.assertIsNone(report.price_bands)
        self.assertIn("starting_fcf_must_be_positive_for_fcf_dcf", report.errors)

    def test_misordered_scenarios_are_flagged(self):
        scenarios = valuation_payload()["scenarios"]
        scenarios[0], scenarios[2] = ({**scenarios[2], "name": "downside"}, {**scenarios[0], "name": "upside"})
        report = ValuationEngine().calculate(valuation_payload(scenarios=scenarios))
        self.assertFalse(report.valid)
        self.assertTrue(any(error.startswith("scenario_order_invalid") for error in report.errors))

    def test_no_cross_check_is_visible(self):
        report = ValuationEngine().calculate(valuation_payload(cross_checks=[]))
        self.assertTrue(report.valid)
        self.assertFalse(report.cross_validation.available)
        self.assertFalse(report.cross_validation.passed)

    def test_earnings_exit_multiple_model_uses_terminal_price_for_expected_return(self):
        report = ValuationEngine().calculate(
            {
                "security_id": "9992.HK",
                "as_of": "2026-07-31",
                "currency": "HKD",
                "method": "earnings_exit_multiple",
                "current_price": "200",
                "starting_earnings": "10",
                "shares_outstanding": "1",
                "forecast_years": 3,
                "cumulative_dividends_per_share": "3",
                "scenarios": [
                    {"name": "downside", "annual_earnings_growth": "0.05", "earnings_exit_multiple": "18", "discount_rate": "0.12"},
                    {"name": "base", "annual_earnings_growth": "0.12", "earnings_exit_multiple": "24", "discount_rate": "0.10"},
                    {"name": "upside", "annual_earnings_growth": "0.18", "earnings_exit_multiple": "30", "discount_rate": "0.09"},
                ],
                "cross_checks": [{"method": "fcf_dcf", "value_per_share": "270"}],
            }
        )
        self.assertTrue(report.valid)
        self.assertIs(report.base.valuation_method, ValuationMethod.EARNINGS_EXIT_MULTIPLE)
        expected_terminal = Decimal("10") * Decimal("1.12") ** 3 * Decimal("24")
        self.assertEqual(report.base.terminal_value_per_share, expected_terminal)
        self.assertEqual(report.base.projected_earnings[-1], Decimal("10") * Decimal("1.12") ** 3)


if __name__ == "__main__":
    unittest.main()
