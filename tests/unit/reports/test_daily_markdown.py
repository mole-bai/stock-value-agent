from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from stock_agent.reports import DailyMarkdownRenderer, render_daily_markdown


def _stock(symbol: str, name: str, price: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "market": "US",
        "price": {
            "value": Decimal(price),
            "currency": "USD",
            "change_pct": Decimal("1.25"),
            "as_of": "2026-07-31T20:00:00-04:00",
            "source_url": f"https://prices.example.test/{symbol}",
            "provisional": True,
        },
        "metrics": {
            "ROIC": {"value": "18.4", "unit": "%", "period": "TTM"},
            "自由现金流": {"value": "1200000000", "currency": "USD"},
        },
        "signals": [
            {
                "severity": "orange",
                "title": "现金转化率下降",
                "detail": "TTM 经营现金流/净利润降至 0.72。",
                "evidence_url": f"https://filings.example.test/{symbol}/10-q",
            }
        ],
        "recommendation": {
            "action": "等待更佳价格",
            "confidence": "中",
            "scope": "公司级研究观点",
            "reason_codes": ["MOS_BELOW_REQUIRED"],
            "reasons": ["基准情景安全边际不足。"],
            "valuation": {
                "bear": {
                    "value": "82",
                    "assumptions": ["收入低增长", "利润率收缩"],
                },
                "base": {"value": "110", "assumptions": ["ROIC 保持稳定"]},
                "bull": {"value": "138", "assumptions": ["利润率温和扩张"]},
                "margin_of_safety": Decimal("0.09"),
                "expected_return_bear": Decimal("-0.04"),
                "expected_return_base": Decimal("0.10"),
                "expected_return_bull": Decimal("0.17"),
            },
            "risks": ["客户集中度较高。"],
            "invalidation": ["ROIC 连续两期低于资本成本。"],
            "valid_until": "下一份财报或重大公告",
            "data_gaps": ["同行一致预期数据未接入。"],
        },
        "events": [
            {
                "title": "发布季度报告",
                "published_at": "2026-07-31T16:10:00-04:00",
                "source_url": f"https://filings.example.test/{symbol}/10-q",
            }
        ],
        "sources": [
            {"label": "公司投资者关系", "url": f"https://ir.example.test/{symbol}"}
        ],
    }


class DailyMarkdownRendererTests(unittest.TestCase):
    def test_renders_complete_three_stock_daily_report(self) -> None:
        result = {
            "run_at": "2026-08-01T07:30:00+08:00",
            "status": "degraded",
            "mode": "daily",
            "stocks": [
                _stock("AAA", "甲公司", "100"),
                _stock("BBB", "乙公司", "55"),
                _stock("CCC", "丙公司", "210"),
            ],
            "warnings": ["美股价格为临时收盘，等待 T+1 校正。"],
        }

        report = render_daily_markdown(result)

        self.assertIn("# 自选股价值投资监控日报", report)
        self.assertIn("运行状态：部分降级", report)
        self.assertIn("股票卡片（3 只）", report)
        self.assertEqual(report.count("#### 价格变化"), 3)
        self.assertIn("### 1. AAA｜甲公司（US）", report)
        self.assertIn("最新价格：100.00 USD（临时价格，待供应商校正）", report)
        self.assertIn("当日变化：+1.25%", report)
        self.assertIn("现金转化率下降", report)
        self.assertIn("行动倾向：等待更佳价格", report)
        self.assertIn("| 悲观 | 82.00 USD", report)
        self.assertIn("| 基准 | 110.00 USD | 9.00% | +10.00%", report)
        self.assertIn("客户集中度较高", report)
        self.assertIn("ROIC 连续两期低于资本成本", report)
        self.assertIn("https://ir.example.test/AAA", report)
        self.assertIn("## 免责声明", report)
        self.assertIn("不构成投资建议", report)

    def test_missing_fields_degrade_instead_of_raising(self) -> None:
        report = DailyMarkdownRenderer().render(
            {
                "status": "success",
                "stocks": [{"symbol": "MISSING", "recommendation": {}}],
            }
        )

        self.assertIn("数据截至：未提供", report)
        self.assertIn("运行时间：未提供", report)
        self.assertIn("最新价格：未提供", report)
        self.assertIn("行动倾向：无建议", report)
        self.assertIn("| 悲观 | 未提供", report)
        self.assertIn("未提供可验证的来源链接", report)

    def test_accepts_dataclass_and_rejects_unsafe_source_scheme(self) -> None:
        @dataclass
        class Result:
            run_at: datetime
            status: str
            mode: str
            stocks: list[dict[str, object]]
            warnings: list[str]

        stock = _stock("SAFE", "安全链接测试", "10")
        stock["sources"] = [{"label": "不安全", "url": "javascript:alert(1)"}]
        stock["price"] = {"source_url": "file:///etc/passwd"}
        stock["signals"] = []
        stock["events"] = []
        report = render_daily_markdown(
            Result(
                run_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                status="success",
                mode="daily",
                stocks=[stock],
                warnings=[],
            )
        )

        self.assertIn("运行状态：完整", report)
        self.assertNotIn("javascript:", report)
        self.assertNotIn("file:///", report)


if __name__ == "__main__":
    unittest.main()
