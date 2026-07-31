from __future__ import annotations

import unittest

from stock_agent.dashboard import DashboardDataError, public_dashboard_payload


class DashboardPayloadTests(unittest.TestCase):
    def test_payload_keeps_ui_fields_and_removes_private_audit_data(self) -> None:
        report = {
            "schema_version": 1,
            "run_at": "2026-07-31T18:30:00+08:00",
            "status": "success",
            "warnings": [],
            "upcoming_events": [{"symbol": "0700.HK", "title": "业绩"}],
            "notifications": [{"local_path": "/private/path"}],
            "stocks": [
                {
                    "symbol": "0700.HK",
                    "name": "腾讯控股",
                    "market": "HK",
                    "price": {
                        "as_of": "2026-07-31T16:00:00+08:00",
                        "change_pct": "1.2",
                        "currency": "HKD",
                        "freshness": "fresh",
                        "provisional": True,
                        "source_url": "https://example.com/price",
                        "value": "475.2",
                        "bid": "475.0",
                    },
                    "metrics": {"营收同比": "9.0%"},
                    "signals": [
                        {
                            "detail": "利润增速快于收入",
                            "evidence_url": "https://example.com/report.pdf",
                            "severity": "information",
                            "title": "利润改善",
                            "internal_id": "secret-signal-id",
                        }
                    ],
                    "recommendation": {
                        "action": "等待",
                        "action_code": "wait",
                        "confidence": "中",
                        "next_review_date": None,
                        "reasons": ["等待安全边际"],
                        "risks": ["监管"],
                        "invalidation": ["现金流恶化"],
                        "valuation": {"base": {"value": "555.3"}},
                        "data_gaps": ["internal-only"],
                    },
                    "audit": {"formula_audit": {"private": True}},
                }
            ],
        }

        payload = public_dashboard_payload(report)

        self.assertNotIn("notifications", payload)
        self.assertNotIn("audit", payload["stocks"][0])
        self.assertNotIn("bid", payload["stocks"][0]["price"])
        self.assertNotIn("data_gaps", payload["stocks"][0]["recommendation"])
        self.assertNotIn("internal_id", payload["stocks"][0]["signals"][0])
        self.assertEqual(payload["stocks"][0]["name"], "腾讯控股")
        self.assertEqual(payload["stocks"][0]["recommendation"]["action"], "等待")

    def test_requires_stock_list(self) -> None:
        with self.assertRaises(DashboardDataError):
            public_dashboard_payload({"stocks": None})


if __name__ == "__main__":
    unittest.main()
