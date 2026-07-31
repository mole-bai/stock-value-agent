"""Command-line entry point for validation, offline replay and live runs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from stock_agent.calendar import CalendarError, load_event_calendar, render_ics
from stock_agent.config import ConfigError, load_fundamentals, load_settings
from stock_agent.connectors import (
    CurlTransport,
    SinaQuoteProvider,
    TencentQuoteProvider,
    YahooChartQuoteProvider,
)
from stock_agent.events import OfficialEventSemanticProvider
from stock_agent.history import SnapshotArchive, SnapshotArchiveError
from stock_agent.orchestration import (
    StockMonitoringPipeline,
    analyze_price_scenario,
    load_static_quote_provider,
)
from stock_agent.delivery import LocalFileDelivery
from stock_agent.review import ReviewDecision, ReviewQueue
from stock_agent.state import JsonStateStore, StateError


DEFAULT_CONFIG = "config/watchlist.json"
DEFAULT_FUNDAMENTALS = "data/fundamentals.json"
DEFAULT_QUOTES = "data/sample_quotes.json"
DEFAULT_EVENTS = "data/events.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-agent",
        description="腾讯、泡泡玛特、贵州茅台的个人价值投资监控日报",
    )
    parser.add_argument("--version", action="version", version="stock-agent 0.2.0")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="校验配置和本地财务快照（不联网）")
    _add_input_arguments(validate)

    offline = commands.add_parser("offline", help="用固定行情快照运行完整离线回放")
    _add_input_arguments(offline)
    _add_run_arguments(offline)
    offline.add_argument(
        "--quotes",
        default=DEFAULT_QUOTES,
        help=f"离线行情快照（默认：{DEFAULT_QUOTES}）",
    )
    offline.add_argument(
        "--now",
        help="可选的 ISO-8601 运行时间；用于可复现回放，必须含时区",
    )

    live = commands.add_parser("live", help="抓取临时行情并生成日报")
    _add_input_arguments(live)
    _add_run_arguments(live)
    live.add_argument(
        "--now",
        help="可选的 ISO-8601 运行时间；通常留空使用当前时间",
    )

    calendar = commands.add_parser(
        "calendar", help="查看未来投资者事件并导出可导入日历的 ICS"
    )
    _add_input_arguments(calendar)
    calendar.add_argument("--days", type=int, default=30, help="查看未来天数（默认：30）")
    calendar.add_argument("--now", help="可选的 ISO-8601 基准时间，必须含时区")
    calendar.add_argument(
        "--ics",
        default="reports/investor-events.ics",
        help="ICS 输出路径（默认：reports/investor-events.ics）",
    )

    history = commands.add_parser("history", help="查看已保存的 point-in-time 运行历史")
    _add_input_arguments(history)
    history.add_argument("--state", help="覆盖配置中的状态文件")
    history.add_argument("--limit", type=int, default=10, help="最多返回条数（默认：10）")

    explain = commands.add_parser("explain", help="解释某只股票最新建议及其变化原因")
    _add_input_arguments(explain)
    explain.add_argument("symbol", help="证券代码，例如 0700.HK")
    explain.add_argument("--state", help="覆盖配置中的状态文件")

    reviews = commands.add_parser("reviews", help="查看新官方文件的人工复核队列")
    _add_input_arguments(reviews)
    reviews.add_argument("--state", help="覆盖配置中的状态文件")
    reviews.add_argument("--symbol", help="只查看某只股票")
    reviews.add_argument("--all", action="store_true", help="包含已经完成的复核")

    review = commands.add_parser("review", help="完成一条官方文件复核并保留审计记录")
    _add_input_arguments(review)
    review.add_argument("review_id", help="reviews 命令显示的 review_id")
    review.add_argument(
        "--decision",
        required=True,
        choices=[item.value for item in ReviewDecision],
        help="material / non_material / data_update_required / duplicate / updated_and_revalued",
    )
    review.add_argument("--note", help="复核说明")
    review.add_argument("--state", help="覆盖配置中的状态文件")
    review.add_argument("--now", help="可选的 ISO-8601 复核时间，必须含时区")

    scenario = commands.add_parser("scenario", help="按假设价格只读重算估值与研究观点")
    _add_input_arguments(scenario)
    scenario.add_argument("symbol", help="证券代码，例如 0700.HK")
    scenario.add_argument("--price", required=True, help="假设价格，必须为正数")
    scenario.add_argument("--now", help="可选的 ISO-8601 测算时间，必须含时区")
    return parser


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, help=f"自选股配置（默认：{DEFAULT_CONFIG}）"
    )
    parser.add_argument(
        "--events",
        default=DEFAULT_EVENTS,
        help=f"投资者事件日历（默认：{DEFAULT_EVENTS}）",
    )
    parser.add_argument(
        "--fundamentals",
        default=DEFAULT_FUNDAMENTALS,
        help=f"官方财务事实快照（默认：{DEFAULT_FUNDAMENTALS}）",
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", help="覆盖配置中的报告目录")
    parser.add_argument("--state", help="覆盖配置中的状态文件")
    parser.add_argument(
        "--no-page-watch",
        action="store_true",
        help="跳过官方公告标题/文档 ID 的语义变化检查",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = load_settings(arguments.config)
        fundamentals = load_fundamentals(arguments.fundamentals)
        events = load_event_calendar(arguments.events)
        _validate_coverage(settings, fundamentals)
        _validate_event_symbols(settings, events.events)
        if arguments.command == "validate":
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "mode": settings.mode,
                        "symbols": [item.symbol for item in settings.watchlist],
                        "fundamentals_observed_at": fundamentals.get("observed_at"),
                        "calendar_events": len(events.events),
                        "message": "配置与本地财务快照校验通过；未访问网络。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        raw_now = getattr(arguments, "now", None)
        now = _parse_now(raw_now) if raw_now else datetime.now(timezone.utc)
        root = Path.cwd()
        if arguments.command == "calendar":
            upcoming = events.upcoming(
                now=now,
                days=arguments.days,
                symbols=(item.symbol for item in settings.watchlist),
            )
            ics_path = _resolve_path(root, arguments.ics)
            receipt = LocalFileDelivery(ics_path.parent).deliver(
                render_ics(upcoming, generated_at=now), filename=ics_path.name
            )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "events": [event.to_dict() for event in upcoming],
                        "due_reminders": [
                            reminder.to_dict() for reminder in events.due_reminders(now=now)
                        ],
                        "ics": str(receipt.path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if arguments.command == "scenario":
            print(
                json.dumps(
                    analyze_price_scenario(
                        settings=settings,
                        fundamentals=fundamentals,
                        symbol=arguments.symbol,
                        price=arguments.price,
                        now=now,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        if arguments.command in {"history", "explain", "reviews", "review"}:
            state_path = _resolve_path(
                root, arguments.state or settings.state_file
            )
            state_store = JsonStateStore(state_path)
            if arguments.command in {"reviews", "review"}:
                state = state_store.load()
                queue = ReviewQueue(state)
                if arguments.command == "reviews":
                    records = queue.items(
                        symbol=arguments.symbol,
                        include_resolved=arguments.all,
                    )
                    print(
                        json.dumps(
                            {
                                "status": "ok",
                                "count": len(records),
                                "reviews": [item.to_dict() for item in records],
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 0
                resolved = queue.resolve(
                    arguments.review_id,
                    decision=arguments.decision,
                    reviewed_at=now,
                    note=arguments.note,
                )
                state_store.save(state)
                print(
                    json.dumps(
                        {"status": "ok", "review": resolved.to_dict()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            archive = SnapshotArchive(state_path.parent / "snapshots")
            if arguments.command == "history":
                if arguments.limit < 0:
                    raise ValueError("--limit 不能为负数")
                identifiers = archive.list_snapshot_ids(newest_first=True)[
                    : arguments.limit
                ]
                rows = []
                for identifier in identifiers:
                    snapshot = archive.load(identifier)
                    rows.append(
                        {
                            "snapshot_id": identifier,
                            "run_at": snapshot.get("run_at"),
                            "status": snapshot.get("status"),
                            "recommendations": {
                                stock.get("symbol"): stock.get("recommendation", {}).get(
                                    "action"
                                )
                                for stock in snapshot.get("stocks", [])
                                if isinstance(stock, dict)
                            },
                            "changed_stock_count": snapshot.get("delta", {})
                            .get("summary", {})
                            .get("changed_stock_count"),
                        }
                    )
                print(
                    json.dumps(
                        {"status": "ok", "count": len(rows), "history": rows},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            latest = archive.load_latest()
            if latest is None:
                raise ValueError("尚无历史快照，请先运行 offline 或 live")
            stock = next(
                (
                    item
                    for item in latest.get("stocks", [])
                    if isinstance(item, dict) and item.get("symbol") == arguments.symbol
                ),
                None,
            )
            if stock is None:
                raise KeyError(f"最新快照不包含 {arguments.symbol}")
            stock_delta = next(
                (
                    item
                    for item in latest.get("delta", {}).get("stocks", [])
                    if isinstance(item, dict) and item.get("symbol") == arguments.symbol
                ),
                None,
            )
            rule_trace = stock.get("audit", {}).get("recommendation", {}).get(
                "rule_trace", []
            )
            failed_rules = [
                item for item in rule_trace if isinstance(item, dict) and not item.get("passed")
            ]
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "run_at": latest.get("run_at"),
                        "symbol": arguments.symbol,
                        "recommendation": stock.get("recommendation"),
                        "change_since_previous": stock_delta,
                        "failed_rules": failed_rules,
                        "sources": stock.get("sources", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        output_dir = _resolve_path(root, arguments.output_dir or settings.output_dir)
        state_path = _resolve_path(root, arguments.state or settings.state_file)

        if arguments.command == "offline":
            providers = [load_static_quote_provider(_resolve_path(root, arguments.quotes))]
            official_pages = None
            semantic_events = None
        else:
            transport = CurlTransport()
            providers = [
                TencentQuoteProvider(transport=transport),
                YahooChartQuoteProvider(http_get=transport),
                SinaQuoteProvider(transport=transport),
            ]
            official_pages = None
            semantic_events = (
                None
                if arguments.no_page_watch
                else OfficialEventSemanticProvider(transport=transport)
            )

        outcome = StockMonitoringPipeline(
            settings=settings,
            fundamentals=fundamentals,
            quote_providers=providers,
            state_store=JsonStateStore(state_path),
            output_dir=output_dir,
            official_page_provider=official_pages,
            event_calendar=events,
            semantic_event_provider=semantic_events,
        ).run(now=now)
        summary = {
            "status": outcome.result["status"],
            "markdown": str(outcome.markdown_receipt.path),
            "json": str(outcome.json_receipt.path),
            "recommendations": {
                stock["symbol"]: stock["recommendation"]["action"]
                for stock in outcome.result["stocks"]
            },
            "changed_stock_count": outcome.result.get("delta", {})
            .get("summary", {})
            .get("changed_stock_count"),
            "pending_reviews": len(outcome.result.get("pending_reviews", [])),
            "notifications_sent": sum(
                bool(item.get("delivery", {}).get("sent"))
                for item in outcome.result.get("notifications", [])
                if isinstance(item, dict)
            ),
            "warnings": outcome.result["warnings"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if outcome.result["status"] != "failed" else 2
    except (
        CalendarError,
        ConfigError,
        SnapshotArchiveError,
        StateError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"stock-agent: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _validate_coverage(settings: Any, fundamentals: dict[str, Any]) -> None:
    stocks = fundamentals["stocks"]
    missing = [item.symbol for item in settings.watchlist if item.symbol not in stocks]
    if missing:
        raise ConfigError(f"财务快照缺少自选股：{', '.join(missing)}")
    for item in settings.watchlist:
        record = stocks[item.symbol]
        if not isinstance(record, dict):
            raise ConfigError(f"{item.symbol} 财务记录必须是对象")
        required = {"latest_period", "facts", "valuation", "sources"}
        absent = sorted(required - set(record))
        if absent:
            raise ConfigError(f"{item.symbol} 财务记录缺少：{', '.join(absent)}")
        valuation = record["valuation"]
        if not isinstance(valuation, dict) or valuation.get("model") != "earnings_exit_multiple":
            raise ConfigError(f"{item.symbol} 当前仅支持 earnings_exit_multiple 估值")
        scenarios = valuation.get("scenarios")
        if not isinstance(scenarios, dict) or set(scenarios) != {"bear", "base", "bull"}:
            raise ConfigError(f"{item.symbol} 必须提供 bear/base/bull 三情景")
        if str(record.get("trade_currency")) != item.currency:
            raise ConfigError(f"{item.symbol} 财务快照与自选股交易币种不一致")


def _validate_event_symbols(settings: Any, events: Sequence[Any]) -> None:
    configured = {item.symbol for item in settings.watchlist}
    unknown = sorted({event.symbol for event in events} - configured)
    if unknown:
        raise ConfigError(f"事件日历包含未配置证券：{', '.join(unknown)}")


def _parse_now(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("--now 必须包含时区，例如 2026-07-31T17:00:00+08:00")
    return value.astimezone(timezone.utc)


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
