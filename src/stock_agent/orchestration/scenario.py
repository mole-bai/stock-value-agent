"""Read-only what-if price analysis using the production valuation rules."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from stock_agent.connectors import Freshness, Quote, StaticQuoteProvider, WATCHLIST_BY_TICKER
from stock_agent.metrics.models import decimal_to_str
from stock_agent.models import AgentSettings
from stock_agent.state import JsonStateStore

from .pipeline import (
    StockMonitoringPipeline,
    _build_valuation,
    _recommendation_mapping,
)


def analyze_price_scenario(
    *,
    settings: AgentSettings,
    fundamentals: Mapping[str, Any],
    symbol: str,
    price: Decimal | str,
    now: datetime,
) -> dict[str, Any]:
    """Recalculate one company view without persisting state or placing orders."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scenario now must be timezone-aware")
    try:
        scenario_price = Decimal(str(price))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("scenario price must be numeric") from exc
    if not scenario_price.is_finite() or scenario_price <= 0:
        raise ValueError("scenario price must be positive and finite")
    watch = next((item for item in settings.watchlist if item.symbol == symbol), None)
    security = WATCHLIST_BY_TICKER.get(symbol)
    financial = fundamentals.get("stocks", {}).get(symbol)
    if watch is None or security is None or not isinstance(financial, Mapping):
        raise KeyError(f"unknown scenario symbol: {symbol}")

    quote = Quote(
        security=security,
        price=scenario_price,
        currency=watch.currency,
        observed_at=now,
        fetched_at=now,
        source_url="https://local.invalid/stock-agent-price-scenario",
        freshness=Freshness.FRESH,
        provisional=True,
        provider="user_price_scenario",
    )
    # Constructing the pipeline performs no I/O; using the same method keeps
    # scenario results exactly aligned with daily recommendation gates.
    pipeline = StockMonitoringPipeline(
        settings=settings,
        fundamentals=fundamentals,
        quote_providers=[StaticQuoteProvider([quote])],
        state_store=JsonStateStore(Path("var/.scenario-unused.json")),
        output_dir=Path("reports"),
    )
    valuation = _build_valuation(watch, financial, quote)
    recommendation = pipeline._recommend(
        watch=watch,
        financial=financial,
        quote=quote,
        valuation=valuation,
        pending_material_event=False,
        checked_at=now,
    )
    mapped = _recommendation_mapping(
        recommendation=recommendation,
        valuation=valuation,
        financial=financial,
        watch=watch,
        data_gaps=[
            "这是用户输入价格的只读情景测算，不代表可成交价格或交易指令。",
            "情景复用当前财务快照与估值假设；未自动模拟新财报、汇率或公司行动。",
        ],
    )
    return {
        "schema_version": 1,
        "run_at": now.isoformat(),
        "symbol": symbol,
        "name": watch.name,
        "hypothetical_price": {
            "value": decimal_to_str(scenario_price),
            "currency": watch.currency,
        },
        "recommendation": mapped,
        "audit": {
            "valuation": valuation.to_dict(),
            "recommendation": recommendation.to_dict(),
        },
        "disclaimer": "只读假设分析；不构成个性化投资建议，不自动交易。",
    }
