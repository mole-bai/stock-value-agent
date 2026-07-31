from __future__ import annotations

import json
import unittest
from decimal import Decimal

from stock_agent.history import (
    ChangeKind,
    ReasonCode,
    SignalTransitionKind,
    compare_runs,
)


def _stock(
    symbol: str,
    *,
    price: object = "100.00",
    metrics: dict[str, object] | None = None,
    events: list[dict[str, object]] | None = None,
    signals: list[dict[str, object]] | None = None,
    action: str = "wait",
    confidence: str = "medium",
    value: object = "120",
    growth: str = "0.10",
    policy: str = "policy-v1",
    quality_passed: bool = False,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "price": {
            "value": price,
            "freshness": "fresh",
            "provisional": True,
            "provider": "prototype",
        },
        "metrics": metrics or {"revenue": "1000", "margin": "0.20"},
        "events": events or [],
        "signals": signals or [],
        "recommendation": {
            "action_code": action,
            "confidence": confidence,
            "valuation": {"base": {"value": value}},
        },
        "audit": {
            "valuation": {
                "formula_version": "valuation-v1",
                "scenarios": {
                    "base": {
                        "intrinsic_value_per_share": value,
                        "assumptions": {"annual_growth": growth},
                    }
                },
            },
            "recommendation": {
                "action": action,
                "confidence": confidence,
                "policy": {"version": policy},
                "rule_trace": [
                    {
                        "rule_id": "entry_rule",
                        "passed": action == "buy_candidate",
                        "actual": str(price),
                        "threshold": "110",
                    }
                ],
                "data_quality": {
                    "passed": quality_passed,
                    "blockers": [] if quality_passed else [{"code": "pending"}],
                },
            },
        },
    }


class RunDeltaTests(unittest.TestCase):
    def test_first_run_is_an_explicit_non_change_baseline(self) -> None:
        current = {
            "run_at": "2026-07-31T09:00:00+00:00",
            "stocks": [
                _stock(
                    "9992.HK",
                    events=[{"event_id": "result", "title": "Results"}],
                    signals=[
                        {"signal_id": "cash", "title": "Cash", "severity": "yellow"}
                    ],
                ),
                _stock("0700.HK"),
            ],
        }

        delta = compare_runs(None, current)
        payload = delta.to_dict()

        self.assertTrue(delta.baseline)
        self.assertEqual([stock.symbol for stock in delta.stocks], ["0700.HK", "9992.HK"])
        self.assertTrue(all(stock.status is ChangeKind.BASELINE for stock in delta.stocks))
        self.assertTrue(all(not stock.has_changes for stock in delta.stocks))
        self.assertTrue(all(not stock.reason_codes for stock in delta.stocks))
        self.assertEqual(payload["summary"]["changed_stock_count"], 0)
        self.assertEqual(payload["summary"]["metric_changes"], 0)
        self.assertEqual(
            payload["stocks"][1]["signals"][0]["transition"], "baseline"
        )
        json.dumps(payload, ensure_ascii=False)

    def test_exact_decimal_changes_signal_lifecycle_and_six_reason_codes(self) -> None:
        previous_a = _stock(
            "0700.HK",
            events=[
                {"event_id": "old", "title": "Old event"},
                {"event_id": "updated", "title": "Before"},
            ],
            signals=[
                {"signal_id": "s-up", "title": "Up", "severity": "yellow"},
                {"signal_id": "s-down", "title": "Down", "severity": "red"},
                {"signal_id": "s-res", "title": "Resolved", "severity": "orange"},
            ],
        )
        current_a = _stock(
            "0700.HK",
            price=Decimal("110.00"),
            metrics={
                "revenue": "1200",
                "margin": Decimal("0.22"),
                "price_to_earnings": "15.5",
            },
            events=[
                {"event_id": "new", "title": "New event"},
                {"event_id": "updated", "title": "After"},
            ],
            signals=[
                {"signal_id": "s-up", "title": "Up", "severity": "red"},
                {"signal_id": "s-down", "title": "Down", "severity": "yellow"},
                {"signal_id": "s-add", "title": "Added", "severity": "information"},
            ],
            action="buy_candidate",
            confidence="high",
            value="135.50",
            growth="0.12",
            policy="policy-v2",
            quality_passed=True,
        )
        unchanged_b_old = _stock("9992.HK", price="50.0", value="60.0")
        unchanged_b_new = _stock("9992.HK", price="50.00", value="60.00")
        previous = {
            "run_at": "2026-07-30T09:00:00+00:00",
            "stocks": [unchanged_b_old, previous_a],
        }
        current = {
            "run_at": "2026-07-31T09:00:00+00:00",
            "stocks": [current_a, unchanged_b_new],
        }

        delta = compare_runs(previous, current)
        changed, unchanged = delta.stocks

        self.assertEqual(changed.symbol, "0700.HK")
        self.assertEqual(changed.price.absolute_change, Decimal("10.00"))
        self.assertEqual(changed.price.percent_change, Decimal("0.1"))
        self.assertEqual(
            [metric.field for metric in changed.metrics],
            ["margin", "price_to_earnings", "revenue"],
        )
        self.assertEqual(
            [(item.event_id, item.kind.value) for item in changed.events],
            [
                ("new", "added"),
                ("old", "removed"),
                ("updated", "changed"),
            ],
        )
        self.assertEqual(
            [(item.signal_id, item.transition) for item in changed.signals],
            [
                ("s-add", SignalTransitionKind.ADDED),
                ("s-down", SignalTransitionKind.DOWNGRADED),
                ("s-res", SignalTransitionKind.RESOLVED),
                ("s-up", SignalTransitionKind.UPGRADED),
            ],
        )
        self.assertEqual(changed.recommendation.action.previous, "wait")
        self.assertEqual(changed.recommendation.action.current, "buy_candidate")
        self.assertEqual(
            changed.recommendation.valuation_center.absolute_change,
            Decimal("15.50"),
        )
        self.assertEqual(
            changed.reason_codes,
            (
                ReasonCode.PRICE,
                ReasonCode.NEW_FUNDAMENTAL,
                ReasonCode.NEW_EVENT,
                ReasonCode.ASSUMPTION,
                ReasonCode.RULE,
                ReasonCode.DATA_QUALITY,
            ),
        )
        self.assertEqual(changed.recommendation.reason_codes, changed.reason_codes)
        self.assertFalse(unchanged.has_changes)
        self.assertEqual(unchanged.price.kind, ChangeKind.UNCHANGED)
        self.assertFalse(unchanged.metrics)

    def test_rule_outcome_change_caused_only_by_price_is_not_rule_change(self) -> None:
        old = _stock("600519.SS", price="100", action="wait")
        new = _stock("600519.SS", price="90", action="buy_candidate")
        delta = compare_runs({"stocks": [old]}, {"stocks": [new]})
        self.assertEqual(delta.stocks[0].reason_codes, (ReasonCode.PRICE,))


if __name__ == "__main__":
    unittest.main()
