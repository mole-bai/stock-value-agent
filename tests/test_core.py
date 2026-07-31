from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.config import ConfigError, load_settings
from stock_agent.state import JsonStateStore


class ConfigTests(unittest.TestCase):
    def test_settings_validate_personal_watchlist(self) -> None:
        value = {
            "mode": "personal_research",
            "report_time": "18:30",
            "recommendation_policy": {"minimum_buy_return": 0.12},
            "watchlist": [
                {
                    "symbol": "0700.HK",
                    "name": "腾讯控股",
                    "market": "HK",
                    "exchange": "XHKG",
                    "currency": "HKD",
                    "timezone": "Asia/Hong_Kong",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            settings = load_settings(path)
        self.assertEqual(settings.watchlist[0].symbol, "0700.HK")
        self.assertEqual(str(settings.policy.minimum_buy_return), "0.12")

    def test_non_personal_mode_is_rejected(self) -> None:
        value = {"mode": "regulated_advice", "watchlist": [{"symbol": "x"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(path)


class StateTests(unittest.TestCase):
    def test_state_round_trip_is_atomic_and_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStateStore(Path(directory) / "state.json")
            state = store.load()
            state["recommendations"]["600519.SS"] = [{"action": "等待"}]
            store.save(state)
            loaded = store.load()
        self.assertEqual(loaded["recommendations"]["600519.SS"][0]["action"], "等待")


if __name__ == "__main__":
    unittest.main()
