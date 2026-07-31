import unittest
from datetime import datetime, timezone
from pathlib import Path

from stock_agent.config import load_fundamentals, load_settings
from stock_agent.orchestration import analyze_price_scenario


ROOT = Path(__file__).resolve().parents[3]


class ScenarioTests(unittest.TestCase):
    def test_lower_price_reuses_production_rules_without_position_advice(self):
        result = analyze_price_scenario(
            settings=load_settings(ROOT / "config/watchlist.json"),
            fundamentals=load_fundamentals(ROOT / "data/fundamentals.json"),
            symbol="0700.HK",
            price="400",
            now=datetime(2026, 7, 31, 9, tzinfo=timezone.utc),
        )
        self.assertEqual(result["symbol"], "0700.HK")
        self.assertIn(result["recommendation"]["action"], {"买入候选", "等待"})
        self.assertNotIn("仓位", str(result["recommendation"]))

    def test_rejects_unknown_symbol_and_non_positive_price(self):
        settings = load_settings(ROOT / "config/watchlist.json")
        fundamentals = load_fundamentals(ROOT / "data/fundamentals.json")
        now = datetime(2026, 7, 31, 9, tzinfo=timezone.utc)
        with self.assertRaises(KeyError):
            analyze_price_scenario(settings=settings, fundamentals=fundamentals, symbol="X", price="1", now=now)
        with self.assertRaises(ValueError):
            analyze_price_scenario(settings=settings, fundamentals=fundamentals, symbol="0700.HK", price="0", now=now)


if __name__ == "__main__":
    unittest.main()
