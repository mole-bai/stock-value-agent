import unittest
from decimal import Decimal

from stock_agent.recommendations import (
    Confidence,
    DataQualityInput,
    RecommendationAction,
    RecommendationEngine,
    RecommendationRequest,
    RiskEvent,
    RiskSeverity,
    ThesisStatus,
    evaluate_data_quality,
)
from stock_agent.valuation import ValuationEngine


QUALITY_PASS = {
    "price_fresh": True,
    "share_count_fresh": True,
    "cash_fresh": True,
    "debt_fresh": True,
    "earnings_fresh": True,
    "cash_flow_fresh": True,
    "required_fields_present": True,
    "source_conflicts_resolved": True,
    "accounting_identity_valid": True,
    "currency_consistent": True,
    "periods_consistent": True,
    "corporate_actions_resolved": True,
    "material_event_pending": False,
    "industry_model_applicable": True,
    "extraction_confidence": "0.95",
}


def make_valuation(price="60", *, cross_checks=True):
    return ValuationEngine().calculate(
        {
            "security_id": "600519.SS",
            "as_of": "2026-07-31",
            "currency": "CNY",
            "current_price": price,
            "starting_fcf": "100",
            "shares_outstanding": "10",
            "net_debt": "0",
            "forecast_years": 5,
            "scenarios": [
                {"name": "downside", "annual_fcf_growth": "0", "discount_rate": "0.12", "terminal_growth": "0.01"},
                {"name": "base", "annual_fcf_growth": "0.05", "discount_rate": "0.10", "terminal_growth": "0.02"},
                {"name": "upside", "annual_fcf_growth": "0.10", "discount_rate": "0.09", "terminal_growth": "0.03"},
            ],
            "cross_checks": ([{"method": "normalized_pe", "value_per_share": "145"}] if cross_checks else []),
            "cross_check_tolerance": "0.25",
            "evidence": {"starting_fcf": ["fact-fcf"], "current_price": ["price"]},
        }
    )


def make_request(price="60", **overrides):
    payload = {
        "company_name": "贵州茅台",
        "valuation": make_valuation(price),
        "data_quality": DataQualityInput.from_dict(QUALITY_PASS),
        "confidence": Confidence.HIGH,
        "thesis_status": ThesisStatus.VALID,
        "existing_position": False,
        "supporting_evidence_ids": ("thesis-evidence",),
        "invalidation_conditions": ("核心产品需求连续两期恶化",),
    }
    payload.update(overrides)
    return RecommendationRequest(**payload)


class RecommendationEngineTests(unittest.TestCase):
    def test_buy_candidate_requires_all_gates(self):
        result = RecommendationEngine().recommend(make_request())
        self.assertIs(result.action, RecommendationAction.BUY_CANDIDATE)
        self.assertTrue(result.data_quality.passed)
        self.assertGreaterEqual(result.margin_of_safety, Decimal("0.25"))
        self.assertGreaterEqual(result.expected_annual_returns["base"], Decimal("0.12"))
        self.assertIn("fact-fcf", result.evidence_ids)
        self.assertEqual(result.to_dict()["action_label_zh"], "买入候选")

    def test_composite_score_blocks_new_exposure_without_hiding_valuation(self):
        result = RecommendationEngine().recommend(
            make_request(composite_score="62", minimum_buy_score="70")
        )

        self.assertIs(result.action, RecommendationAction.WAIT)
        score_rule = next(
            rule for rule in result.rule_trace if rule.rule_id == "minimum_composite_score"
        )
        self.assertFalse(score_rule.passed)
        self.assertIsNotNone(result.scenario_values)

    def test_wait_hold_and_reduce_are_position_aware(self):
        cases = [
            ("130", False, RecommendationAction.WAIT),
            ("105", True, RecommendationAction.HOLD),
            ("200", True, RecommendationAction.REDUCE_CANDIDATE),
        ]
        for price, existing, expected in cases:
            with self.subTest(price=price, existing=existing):
                result = RecommendationEngine().recommend(make_request(price=price, existing_position=existing))
                self.assertIs(result.action, expected)

    def test_quality_gate_fails_closed(self):
        quality = DataQualityInput.from_dict({**QUALITY_PASS, "cash_flow_fresh": False})
        result = RecommendationEngine().recommend(make_request(data_quality=quality))
        self.assertIs(result.action, RecommendationAction.NO_RECOMMENDATION)
        self.assertTrue(any(issue.code == "cash_flow_fresh" for issue in result.data_quality.blockers))
        self.assertFalse(evaluate_data_quality(DataQualityInput()).passed)

    def test_red_hard_risk_overrides_deep_undervaluation(self):
        result = RecommendationEngine().recommend(
            make_request(
                price="20",
                risk_events=(RiskEvent(code="going_concern", description="持续经营重大疑虑", severity=RiskSeverity.RED, evidence_ids=("audit-report",)),),
            )
        )
        self.assertIs(result.action, RecommendationAction.RISK_AVOIDANCE)
        self.assertIn("audit-report", result.evidence_ids)

    def test_hard_risk_plus_bad_data_hides_false_precision(self):
        quality = DataQualityInput.from_dict({**QUALITY_PASS, "source_conflicts_resolved": False})
        result = RecommendationEngine().recommend(
            make_request(data_quality=quality, risk_events=(RiskEvent(code="restatement", description="重大财务重述", severity="red"),))
        )
        self.assertIs(result.action, RecommendationAction.RISK_AVOIDANCE)
        self.assertIsNone(result.scenario_values)
        self.assertIsNotNone(result.valuation_suppressed_reason)

    def test_missing_cross_validation_and_low_confidence_freeze_advice(self):
        without_cross = make_request(valuation=make_valuation(cross_checks=False))
        self.assertIs(RecommendationEngine().recommend(without_cross).action, RecommendationAction.NO_RECOMMENDATION)
        low = make_request(confidence=Confidence.LOW)
        self.assertIs(RecommendationEngine().recommend(low).action, RecommendationAction.NO_RECOMMENDATION)

    def test_dict_input_is_deterministic(self):
        payload = {
            "company_name": "贵州茅台",
            "valuation": make_valuation().to_dict(),
            "data_quality": QUALITY_PASS,
            "confidence": "high",
            "thesis_status": "valid",
            "supporting_evidence_ids": ["thesis-evidence"],
        }
        first = RecommendationEngine().recommend(payload)
        second = RecommendationEngine().recommend(payload)
        self.assertIs(first.action, RecommendationAction.BUY_CANDIDATE)
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
