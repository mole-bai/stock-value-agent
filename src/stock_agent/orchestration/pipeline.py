"""Join quote, official-page, valuation, recommendation and report components.

The orchestration layer intentionally remains dependency-free.  Financial facts
and valuation assumptions are loaded from a reviewed JSON seed; every market
observation and recommendation retains its provenance and rule trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from stock_agent.assessment import AssessmentEngine, ValueScorecard
from stock_agent.calendar import EventCalendar
from stock_agent.connectors import (
    ConnectorError,
    Freshness,
    OfficialPageProvider,
    Quote,
    QuoteProvider,
    Security,
    StaticQuoteProvider,
    WATCHLIST_BY_TICKER,
    classify_freshness,
)
from stock_agent.delivery import DeliveryReceipt, LocalFileDelivery
from stock_agent.events import (
    EventDiffStatus,
    EventScanStatus,
    OfficialEventSemanticProvider,
    SemanticEventSnapshot,
    diff_event_snapshots,
)
from stock_agent.history import SnapshotArchive, compare_runs
from stock_agent.metrics.models import decimal_to_str
from stock_agent.models import AgentSettings, WatchItem
from stock_agent.notifications import (
    LocalOutbox,
    NotificationLedger,
    NotificationPriority,
    QuietHours,
    build_run_notifications,
)
from stock_agent.recommendations import (
    Confidence,
    DataQualityInput,
    EvidenceRef,
    RecommendationEngine,
    RecommendationPolicy,
    RecommendationRequest,
    RecommendationResult,
    ThesisStatus,
)
from stock_agent.reports import render_daily_markdown
from stock_agent.review import ReviewQueue
from stock_agent.state import JsonStateStore
from stock_agent.valuation import ScenarioName, ValuationEngine, ValuationReport


DISCLAIMER = (
    "仅用于个人研究的公司级条件性观点，不构成个性化投资建议、证券要约或收益保证；"
    "不包含仓位、交易数量或自动下单。"
)


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    result: dict[str, Any]
    markdown_receipt: DeliveryReceipt
    json_receipt: DeliveryReceipt


class QuoteChain:
    """Try providers in order and retain a short, user-safe failure trace."""

    def __init__(self, providers: Iterable[QuoteProvider]) -> None:
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("at least one quote provider is required")

    def get_latest(
        self, security: Security, *, now: datetime
    ) -> tuple[Quote | None, tuple[str, ...]]:
        failures: list[str] = []
        for provider in self.providers:
            name = getattr(provider, "provider_name", type(provider).__name__)
            try:
                quote = provider.get_latest(security, now=now)
            except (ConnectorError, OSError, ValueError) as exc:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if quote.price <= 0:
                failures.append(f"{name}: non-positive quote")
                continue
            return quote, tuple(failures)
        return None, tuple(failures)


def load_static_quote_provider(path: str | Path) -> StaticQuoteProvider:
    """Load a deterministic quote replay while preserving timestamps."""

    document = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=str)
    raw_quotes = document.get("quotes") if isinstance(document, Mapping) else None
    if not isinstance(raw_quotes, Mapping) or not raw_quotes:
        raise ValueError("sample quote file requires a non-empty quotes mapping")
    quotes: list[Quote] = []
    for ticker, raw_value in raw_quotes.items():
        raw = dict(raw_value)
        try:
            security = WATCHLIST_BY_TICKER[str(ticker)]
            freshness = Freshness(str(raw.get("freshness", "unknown")))
            quotes.append(
                Quote(
                    security=security,
                    price=_decimal(raw["price"]),
                    currency=str(raw["currency"]),
                    observed_at=datetime.fromisoformat(str(raw["observed_at"])),
                    fetched_at=datetime.fromisoformat(str(raw["fetched_at"])),
                    source_url=str(raw["source_url"]),
                    freshness=freshness,
                    provisional=bool(raw.get("provisional", True)),
                    provider=str(raw.get("provider", "static_quotes")),
                    previous_close=_optional_decimal(raw.get("previous_close")),
                    open=_optional_decimal(raw.get("open")),
                    high=_optional_decimal(raw.get("high")),
                    low=_optional_decimal(raw.get("low")),
                    bid=_optional_decimal(raw.get("bid")),
                    ask=_optional_decimal(raw.get("ask")),
                    turnover=_optional_decimal(raw.get("turnover")),
                    volume=int(raw["volume"]) if raw.get("volume") is not None else None,
                    raw_symbol=str(raw["raw_symbol"]) if raw.get("raw_symbol") else None,
                )
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError(f"invalid static quote for {ticker}: {exc}") from exc
    return StaticQuoteProvider(quotes)


class StockMonitoringPipeline:
    """Execute one idempotent-ish daily monitoring snapshot."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        fundamentals: Mapping[str, Any],
        quote_providers: Iterable[QuoteProvider],
        state_store: JsonStateStore,
        output_dir: str | Path,
        official_page_provider: OfficialPageProvider | None = None,
        event_calendar: EventCalendar | None = None,
        semantic_event_provider: OfficialEventSemanticProvider | None = None,
        snapshot_archive: SnapshotArchive | None = None,
        notification_outbox: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self.fundamentals = fundamentals
        self.quotes = QuoteChain(quote_providers)
        self.state_store = state_store
        self.output_dir = Path(output_dir)
        self.official_pages = official_page_provider
        self.event_calendar = event_calendar
        self.semantic_events = semantic_event_provider
        self.snapshot_archive = snapshot_archive or SnapshotArchive(
            state_store.path.parent / "snapshots"
        )
        self.notification_outbox = Path(notification_outbox) if notification_outbox else self.output_dir / "notifications"
        self.valuation_engine = ValuationEngine()
        self.recommendation_engine = RecommendationEngine()

    def run(self, *, now: datetime | None = None) -> PipelineOutcome:
        checked_at = _aware_utc(now or datetime.now(timezone.utc))
        local_now = checked_at.astimezone(ZoneInfo(self.settings.timezone))
        state = self.state_store.load()
        fundamentals_by_symbol = self.fundamentals.get("stocks", {})
        warnings: list[str] = []
        stock_results: list[dict[str, Any]] = []

        for watch in self.settings.watchlist:
            security = WATCHLIST_BY_TICKER.get(watch.symbol)
            financial = fundamentals_by_symbol.get(watch.symbol)
            if security is None or not isinstance(financial, Mapping):
                warnings.append(f"{watch.symbol}: 缺少证券映射或财务种子，已跳过。")
                continue
            stock_result, stock_warnings = self._run_stock(
                watch=watch,
                security=security,
                financial=dict(financial),
                state=state,
                checked_at=checked_at,
            )
            stock_results.append(stock_result)
            warnings.extend(stock_warnings)

        failed_prices = sum(1 for stock in stock_results if not stock.get("price", {}).get("value"))
        status = "success"
        if warnings or failed_prices:
            status = "degraded"
        if stock_results and failed_prices == len(stock_results):
            status = "failed"

        upcoming_events = (
            self.event_calendar.upcoming(
                now=checked_at,
                days=30,
                symbols=(watch.symbol for watch in self.settings.watchlist),
            )
            if self.event_calendar is not None
            else ()
        )
        calendar_reminders = (
            self.event_calendar.due_reminders(now=checked_at)
            if self.event_calendar is not None
            else ()
        )
        result: dict[str, Any] = {
            "schema_version": 1,
            "run_at": local_now.isoformat(),
            "data_as_of": "；".join(
                f"{stock['symbol']} {stock.get('price', {}).get('as_of', '价格缺失')}"
                for stock in stock_results
            ),
            "status": status,
            "mode": "个人研究 / 公司级观点",
            "stocks": stock_results,
            "upcoming_events": [event.to_dict() for event in upcoming_events],
            "calendar_reminders": [reminder.to_dict() for reminder in calendar_reminders],
            "pending_reviews": [
                item.to_dict() for item in ReviewQueue(state).pending()
            ],
            "warnings": warnings,
            "disclaimers": [DISCLAIMER],
        }

        previous_snapshot = self.snapshot_archive.load_latest()
        run_delta = compare_runs(previous_snapshot, result)
        result["delta"] = run_delta.to_dict()

        generated_notifications = build_run_notifications(result, now=checked_at)
        notification_ledger = NotificationLedger(state)
        notification_settings = self.settings.notifications
        quiet_hours = QuietHours(
            timezone=self.settings.timezone,
            start=notification_settings.quiet_start,
            end=notification_settings.quiet_end,
            bypass_through=NotificationPriority[
                notification_settings.bypass_through
            ],
        )
        notification_results: list[dict[str, Any]] = []
        outbox = LocalOutbox(
            self.notification_outbox,
            write_markdown=notification_settings.write_markdown,
        )
        for notification in generated_notifications:
            if not notification_settings.enabled:
                notification_results.append(
                    {
                        **notification.to_dict(),
                        "delivery": {
                            "sent": False,
                            "deferred": False,
                            "reason": "notifications_disabled",
                        },
                    }
                )
                continue
            decision = notification_ledger.decide(
                notification, now=checked_at, quiet_hours=quiet_hours
            )
            record = {
                **notification.to_dict(),
                "delivery": {
                    "sent": decision.send,
                    "deferred": decision.deferred,
                    "reason": decision.reason,
                },
            }
            if decision.send:
                receipt = outbox.deliver(notification)
                notification_ledger.mark_sent(notification, sent_at=checked_at)
                record["delivery"].update(
                    {
                        "json": str(receipt.json.path),
                        "markdown": str(receipt.markdown.path)
                        if receipt.markdown is not None
                        else None,
                    }
                )
            notification_results.append(record)
        result["notifications"] = notification_results

        renderer_output = render_daily_markdown(result)
        delivery = LocalFileDelivery(self.output_dir)
        day = local_now.date().isoformat()
        markdown_receipt = delivery.deliver(
            renderer_output, filename=f"daily-{day}.md"
        )
        json_receipt = delivery.deliver(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            filename=f"daily-{day}.json",
        )
        snapshot_id = self.snapshot_archive.save(result)

        state["last_run_at"] = checked_at.isoformat()
        runs = list(state.setdefault("runs", []))
        runs.append(
            {
                "run_at": checked_at.isoformat(),
                "status": status,
                "markdown": str(markdown_receipt.path),
                "json": str(json_receipt.path),
                "warnings": len(warnings),
                "snapshot_id": snapshot_id,
                "notifications_sent": sum(
                    bool(item["delivery"]["sent"]) for item in notification_results
                ),
            }
        )
        state["runs"] = runs[-100:]
        self.state_store.save(state)
        return PipelineOutcome(
            result=result,
            markdown_receipt=markdown_receipt,
            json_receipt=json_receipt,
        )

    def _run_stock(
        self,
        *,
        watch: WatchItem,
        security: Security,
        financial: dict[str, Any],
        state: dict[str, Any],
        checked_at: datetime,
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        events: list[dict[str, Any]] = list(financial.get("events", []))
        quote, quote_failures = self.quotes.get_latest(security, now=checked_at)
        if quote is None:
            failure_detail = " | ".join(quote_failures) or "未返回有效价格"
            warnings.append(
                f"{watch.symbol}: 所有行情源均不可用，估值与正面建议被冻结："
                f"{failure_detail}"
            )
        else:
            quote = replace(
                quote,
                freshness=classify_freshness(
                    quote.observed_at, checked_at, timedelta(days=4)
                ),
            )
            if quote.freshness is not Freshness.FRESH:
                warnings.append(
                    f"{watch.symbol}: 行情时间为 {quote.observed_at.isoformat()}，"
                    "已超过允许时效，正面建议被冻结。"
                )
            if quote.currency.upper() != watch.currency.upper():
                warnings.append(
                    f"{watch.symbol}: 行情币种 {quote.currency} 与配置币种 "
                    f"{watch.currency} 不一致，正面建议被冻结。"
                )

        review_queue = ReviewQueue(state)
        pending_material_event = review_queue.blocks_positive_view(watch.symbol)
        if pending_material_event:
            warnings.append(
                f"{watch.symbol}: 仍有 {len(review_queue.pending(symbol=watch.symbol))} "
                "条官方文件未完成复核/重估，正面建议保持冻结。"
            )
        semantic_audit: list[dict[str, Any]] = []
        if self.semantic_events is not None:
            try:
                snapshots = self.semantic_events.scan_security(
                    security, now=checked_at
                )
                extracted_count = 0
                semantic_state = state.setdefault("event_semantics", {})
                scan_state = state.setdefault("event_scan_audit", {})
                for current in snapshots:
                    raw_previous = semantic_state.get(current.source_id)
                    previous = None
                    if isinstance(raw_previous, Mapping):
                        try:
                            previous = SemanticEventSnapshot.from_dict(raw_previous)
                        except (KeyError, TypeError, ValueError) as exc:
                            warnings.append(
                                f"{watch.symbol}: 公告语义基线损坏，已重新建基线："
                                f"{type(exc).__name__}: {exc}"
                            )
                    change = diff_event_snapshots(previous, current)
                    semantic_audit.append(
                        {
                            "source_id": current.source_id,
                            "source_label": current.source_label,
                            "source_url": current.source_url,
                            "scan_status": current.status.value,
                            "diff_status": change.status.value,
                            "message": change.message,
                            "semantic_hash": current.semantic_hash,
                            "new_document_ids": [
                                item.document_id for item in change.new_events
                            ],
                            "updated_document_ids": [
                                item.document_id for item in change.updated_events
                            ],
                            "removed_document_ids": list(
                                change.removed_document_ids
                            ),
                        }
                    )
                    scan_state[current.source_id] = {
                        "status": current.status.value,
                        "observed_at": current.observed_at.isoformat(),
                        "message": current.message,
                        "source_url": current.source_url,
                    }
                    if current.status is EventScanStatus.EXTRACTED:
                        extracted_count += 1
                        # Only a successful semantic parse advances last-good.
                        semantic_state[current.source_id] = current.to_dict()
                    else:
                        # Retain source-level degradation in the audit trail.
                        # A redundant source must not degrade the whole stock
                        # when another authoritative source is comparable.
                        pass

                    if change.status is EventDiffStatus.CHANGED:
                        for item, update_kind in (
                            *((event, "new") for event in change.new_events),
                            *((event, "updated") for event in change.updated_events),
                        ):
                            document_key = item.document_id
                            if update_kind == "updated":
                                document_key = (
                                    f"{item.document_id}:updated:"
                                    f"{current.semantic_hash[:12]}"
                                )
                            review_queue.enqueue(
                                symbol=watch.symbol,
                                document_id=document_key,
                                title=item.title,
                                published_at=(
                                    item.published_date.isoformat()
                                    if item.published_date
                                    else None
                                ),
                                source_url=item.document_url,
                                discovered_at=checked_at,
                            )
                            events.append(
                                {
                                    "event_id": f"{watch.symbol}:{document_key}",
                                    "document_id": item.document_id,
                                    "title": item.title,
                                    "published_at": (
                                        item.published_date.isoformat()
                                        if item.published_date
                                        else checked_at.isoformat()
                                    ),
                                    "source_url": item.document_url,
                                    "review_status": "pending",
                                    "change_kind": update_kind,
                                }
                            )
                        if change.new_events or change.updated_events:
                            pending_material_event = True
                            warnings.append(
                                f"{watch.symbol}: 检测到官方文件新增或更新，"
                                "已进入人工复核队列并冻结正面建议。"
                            )
                        if change.removed_document_ids:
                            warnings.append(
                                f"{watch.symbol}: {current.source_label} 有 "
                                f"{len(change.removed_document_ids)} 条记录从当前索引移除；"
                                "可能是分页滚动，已留审计记录。"
                            )
                # Freeze and warn only when no configured official source
                # produced a comparable list. Individual source failures remain
                # visible in ``official_event_semantics`` for diagnosis.
                if snapshots and extracted_count == 0:
                    pending_material_event = True
                    warnings.append(
                        f"{watch.symbol}: 所有官方公告入口均不可比较，正面建议被冻结。"
                    )
            except (OSError, TypeError, ValueError) as exc:
                pending_material_event = True
                warnings.append(
                    f"{watch.symbol}: 官方公告语义监控失败（不等同于没有公告）："
                    f"{type(exc).__name__}: {exc}"
                )
        elif self.official_pages is not None:
            try:
                snapshot = self.official_pages.get_snapshot(security, now=checked_at)
                old_snapshot = state.setdefault("source_hashes", {}).get(watch.symbol)
                if old_snapshot and old_snapshot.get("content_hash") != snapshot.content_hash:
                    pending_material_event = True
                    events.append(
                        {
                            "title": "官方披露页内容发生变化，等待人工语义复核",
                            "published_at": snapshot.observed_at.isoformat(),
                            "source_url": snapshot.source_url,
                        }
                    )
                state["source_hashes"][watch.symbol] = {
                    "content_hash": snapshot.content_hash,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "source_url": snapshot.source_url,
                    "etag": snapshot.etag,
                    "last_modified": snapshot.last_modified,
                }
            except (ConnectorError, OSError, ValueError) as exc:
                warnings.append(
                    f"{watch.symbol}: 官方披露页监控失败（不等同于没有公告）："
                    f"{type(exc).__name__}: {exc}"
                )

        pending_material_event = (
            pending_material_event
            or review_queue.blocks_positive_view(watch.symbol)
        )

        price_mapping = _quote_mapping(quote, watch) if quote is not None else {
            "value": None,
            "currency": watch.currency,
            "change_pct": None,
            "as_of": None,
            "source_url": None,
            "provisional": True,
            "freshness": "unknown",
        }
        if quote is not None:
            state.setdefault("quotes", {})[watch.symbol] = price_mapping

        valuation: ValuationReport | None = None
        recommendation: RecommendationResult | None = None
        assessment: ValueScorecard | None = None
        data_gaps = [
            "行情源为个人原型的非官方临时数据；生产或对外分发前必须更换为持牌行情。",
            "港股财务币种换算使用配置中的静态汇率假设，并非实时外汇报价。"
            if watch.market == "HK"
            else "估值只反映当前配置假设，未纳入个人税务、流动性和组合约束。",
            (
                "估值采用盈利退出倍数三情景模型；价值评分纳入盈利、增长、现金流、"
                "资产负债表、资本配置、估值及风险，但尚无独立估值模型交叉验证。"
            ),
            (
                "公告监控按标题、稳定文档 URL 和文档 ID 做语义去噪；"
                "单个冗余入口异常仅记入审计；所有官方入口均不可比较时才冻结建议。"
                if self.semantic_events is not None
                else "本次未启用公告语义监控；需人工核对官方公告入口。"
            ),
        ]
        if quote is not None:
            try:
                valuation = _build_valuation(watch, financial, quote)
                assessment = AssessmentEngine().compute(financial, valuation)
                recommendation = self._recommend(
                    watch=watch,
                    financial=financial,
                    quote=quote,
                    valuation=valuation,
                    assessment=assessment,
                    pending_material_event=pending_material_event,
                    checked_at=checked_at,
                )
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                warnings.append(
                    f"{watch.symbol}: 估值或建议失败，已冻结结论：{type(exc).__name__}: {exc}"
                )

        recommendation_mapping = _recommendation_mapping(
            recommendation=recommendation,
            valuation=valuation,
            financial=financial,
            watch=watch,
            data_gaps=data_gaps,
            assessment=assessment,
        )
        rec_history = list(state.setdefault("recommendations", {}).get(watch.symbol, []))
        rec_history.append(
            {
                "as_of": checked_at.isoformat(),
                "action": recommendation_mapping["action"],
                "confidence": recommendation_mapping["confidence"],
                "price": price_mapping.get("value"),
            }
        )
        state["recommendations"][watch.symbol] = rec_history[-50:]

        sources = _merge_sources(financial.get("sources", []), watch.sources)
        first_source = sources[0]["url"] if sources else None
        signals = []
        for raw_signal in financial.get("signals", []):
            signal = dict(raw_signal)
            signal["severity"] = {
                "positive": "information",
                "watch": "yellow",
                "critical": "red",
            }.get(str(signal.get("severity", "")).lower(), signal.get("severity", "information"))
            signal.setdefault("evidence_url", first_source)
            signals.append(signal)

        stock_result = {
            "symbol": watch.symbol,
            "name": watch.name,
            "market": watch.market,
            "price": price_mapping,
            "data_status": {
                "quote_freshness": price_mapping.get("freshness", "unknown"),
                "financial_period": str(financial.get("latest_period", "")),
                "financial_period_type": str(
                    financial.get("latest_period_type", "unknown")
                ),
                "fundamentals_snapshot_at": self.fundamentals.get("observed_at"),
                "financial_period_fresh": _fundamental_period_fresh(
                    str(financial.get("latest_period", "")),
                    str(financial.get("latest_period_type", "annual")),
                    checked_at,
                ),
                "official_source_count": len(financial.get("sources", [])),
                "announcement_monitor": (
                    "semantic"
                    if self.semantic_events is not None
                    else "legacy_byte"
                    if self.official_pages is not None
                    else "disabled"
                ),
                "announcement_sources_extracted": sum(
                    item.get("scan_status") == EventScanStatus.EXTRACTED.value
                    for item in semantic_audit
                ),
                "announcement_sources_total": len(semantic_audit),
                "pending_review_count": len(
                    review_queue.pending(symbol=watch.symbol)
                ),
                "valuation_model": str(financial.get("valuation", {}).get("model", "")),
            },
            "metrics": _display_metrics(financial, quote),
            "events": events,
            "signals": signals,
            "recommendation": recommendation_mapping,
            "risks": list(watch.risks),
            "invalidation": list(watch.invalidation),
            "sources": sources,
            "audit": {
                "quote": {
                    "selected_provider": quote.provider if quote is not None else None,
                    "fallback_failures": list(quote_failures),
                },
                "valuation": valuation.to_dict() if valuation else None,
                "assessment": assessment.to_dict() if assessment else None,
                "recommendation": recommendation.to_dict() if recommendation else None,
                "official_event_semantics": semantic_audit,
            },
        }
        return stock_result, warnings

    def _recommend(
        self,
        *,
        watch: WatchItem,
        financial: Mapping[str, Any],
        quote: Quote,
        valuation: ValuationReport,
        assessment: ValueScorecard,
        pending_material_event: bool,
        checked_at: datetime,
    ) -> RecommendationResult:
        period_fresh = _fundamental_period_fresh(
            str(financial["latest_period"]), str(financial.get("latest_period_type", "annual")), checked_at
        )
        source_ids = tuple(
            f"official:{watch.symbol}:{index}"
            for index, _source in enumerate(financial.get("sources", []), start=1)
        )
        data_quality = DataQualityInput(
            price_fresh=quote.freshness is Freshness.FRESH,
            share_count_fresh=period_fresh,
            cash_fresh=period_fresh,
            debt_fresh=period_fresh,
            earnings_fresh=period_fresh,
            cash_flow_fresh=period_fresh,
            required_fields_present=True,
            source_conflicts_resolved=True,
            accounting_identity_valid=True,
            currency_consistent=quote.currency.upper() == watch.currency.upper(),
            periods_consistent=True,
            corporate_actions_resolved=True,
            material_event_pending=pending_material_event,
            industry_model_applicable=True,
            extraction_confidence=Decimal("0.90"),
        )
        valuation_config = dict(financial["valuation"])
        policy = RecommendationPolicy(
            version="company_research_policy.value_scorecard.v2",
            target_annual_return=assessment.adjusted_target_return,
            minimum_hold_annual_return=_decimal(
                valuation_config.get("minimum_hold_return", self.settings.policy.minimum_hold_return)
            ),
            required_margin_of_safety=assessment.adjusted_margin_of_safety,
            minimum_confidence=Confidence.MEDIUM,
            require_cross_validation=False,
        )
        evidence = tuple(
            EvidenceRef(
                evidence_id=evidence_id,
                summary=str(source.get("label", "官方财报")),
                category="official_filing",
                source_url=str(source.get("url")),
                observed_at=str(financial.get("latest_period")),
            )
            for evidence_id, source in zip(source_ids, financial.get("sources", []))
        )
        request = RecommendationRequest(
            company_name=watch.name,
            valuation=valuation,
            data_quality=data_quality,
            confidence=Confidence(str(financial.get("confidence", "medium"))),
            thesis_status=ThesisStatus.VALID,
            existing_position=False,
            investment_case_qualified=assessment.quality_qualified,
            composite_score=assessment.composite_score,
            minimum_buy_score=assessment.minimum_buy_score,
            evidence=evidence,
            supporting_evidence_ids=source_ids,
            risks=watch.risks,
            invalidation_conditions=watch.invalidation,
            valid_until="下一份财报、重大公告或价格离开当前行动区间中的较早者",
            next_review_date=str(financial.get("facts", {}).get("next_results_at"))
            if financial.get("facts", {}).get("next_results_at")
            else None,
            policy=policy,
        )
        return self.recommendation_engine.recommend(request)


def _build_valuation(
    watch: WatchItem, financial: Mapping[str, Any], quote: Quote
) -> ValuationReport:
    config = dict(financial["valuation"])
    eps = _decimal(config["base_earnings_per_share"])
    fx = _decimal(config.get("fx_to_trade_currency", "1"))
    starting_earnings_trade_currency = eps * fx
    years = int(config.get("horizon_years", 5))
    dividend = _decimal(config.get("dividend_per_share_trade_currency", "0"))
    discount = _decimal(config["discount_rate"])
    scenario_payload = []
    for raw_name, canonical_name in (
        ("bear", "downside"),
        ("base", "base"),
        ("bull", "upside"),
    ):
        raw = dict(config["scenarios"][raw_name])
        scenario_payload.append(
            {
                "name": canonical_name,
                "annual_earnings_growth": raw["growth_rate"],
                "earnings_exit_multiple": raw["exit_multiple"],
                "discount_rate": discount,
                "annual_share_dilution": "0",
                "contextual_drivers": {"fx_to_trade_currency": fx},
                "evidence_ids": [f"assumption:{watch.symbol}:{raw_name}"],
            }
        )
    source_ids = [
        f"official:{watch.symbol}:{index}"
        for index, _source in enumerate(financial.get("sources", []), start=1)
    ]
    request = {
        "security_id": watch.symbol,
        "as_of": quote.observed_at.isoformat(),
        "currency": watch.currency,
        "method": "earnings_exit_multiple",
        "current_price": quote.price,
        "starting_earnings": starting_earnings_trade_currency,
        "shares_outstanding": "1",
        "net_debt": "0",
        "forecast_years": years,
        "cumulative_dividends_per_share": dividend * Decimal(years),
        "required_margin_of_safety": config["margin_of_safety"],
        "overvaluation_premium": "0.20",
        "scenarios": scenario_payload,
        "cross_checks": [],
        "evidence": {
            "starting_earnings": source_ids,
            "current_price": [f"quote:{watch.symbol}:{quote.observed_at.isoformat()}"],
            "fx_assumption": [f"assumption:{watch.symbol}:fx"],
        },
    }
    return ValuationEngine().calculate(request)


def _recommendation_mapping(
    *,
    recommendation: RecommendationResult | None,
    valuation: ValuationReport | None,
    financial: Mapping[str, Any],
    watch: WatchItem,
    data_gaps: list[str],
    assessment: ValueScorecard | None,
) -> dict[str, Any]:
    if recommendation is None or valuation is None:
        return {
            "action": "无建议",
            "scope": "公司级研究观点",
            "confidence": "数据不足",
            "reasons": ["行情、估值或关键财务数据未通过质量门槛。"],
            "reason_codes": ["pipeline_incomplete"],
            "valuation": {},
            "assessment": assessment.to_dict() if assessment else {},
            "risks": list(watch.risks),
            "invalidation": list(watch.invalidation),
            "data_gaps": data_gaps,
        }

    action = recommendation.action.label_zh
    base = valuation.base
    reasons: list[str] = []
    if assessment is not None:
        reasons.append(
            f"价值综合评分 {assessment.composite_score}/100，质量评分 "
            f"{assessment.quality_score}/100，覆盖率 "
            f"{(assessment.overall_coverage * 100).quantize(Decimal('0.1'))}%。"
        )
    if base is not None and valuation.price_bands is not None:
        reasons.append(
            f"基准内在价值约 {base.intrinsic_value_per_share.quantize(Decimal('0.01'))} "
            f"{watch.currency}，建仓价上限约 "
            f"{recommendation.price_bands['entry_price_ceiling'].quantize(Decimal('0.01'))} {watch.currency}。"
        )
        if base.expected_annual_return is not None:
            reasons.append(
                f"基准情景五年期年化总回报约 "
                f"{(base.expected_annual_return * 100).quantize(Decimal('0.01'))}%。"
            )
    reasons.extend(_translate_rationale(item) for item in recommendation.rationale)
    reason_codes = [rule.rule_id for rule in recommendation.rule_trace if not rule.passed]
    scenario_config = dict(financial["valuation"])["scenarios"]
    valuation_mapping: dict[str, Any] = {
        "margin_of_safety": decimal_to_str(recommendation.margin_of_safety),
    }
    for canonical, output_key, config_key in (
        (ScenarioName.DOWNSIDE, "bear", "bear"),
        (ScenarioName.BASE, "base", "base"),
        (ScenarioName.UPSIDE, "bull", "bull"),
    ):
        scenario = valuation.scenarios.get(canonical)
        raw_assumption = scenario_config[config_key]
        valuation_mapping[output_key] = (
            {
                "value": decimal_to_str(scenario.intrinsic_value_per_share),
                "currency": valuation.currency,
                "margin_of_safety": decimal_to_str(scenario.margin_of_safety),
                "expected_return": decimal_to_str(scenario.expected_annual_return),
                "assumptions": [
                    f"EPS 年增速 {(_decimal(raw_assumption['growth_rate']) * 100).quantize(Decimal('0.1'))}%",
                    f"退出市盈率 {raw_assumption['exit_multiple']} 倍",
                    f"折现率 {(_decimal(financial['valuation']['discount_rate']) * 100).quantize(Decimal('0.1'))}%",
                ],
            }
            if scenario is not None
            else None
        )
    return {
        "action": action,
        "action_code": recommendation.action.value,
        "scope": "公司级研究观点",
        "confidence": {"high": "高", "medium": "中", "low": "低", "insufficient": "数据不足"}[
            recommendation.confidence.value
        ],
        "reasons": list(dict.fromkeys(reasons)),
        "reason_codes": reason_codes,
        "valuation": valuation_mapping,
        "assessment": assessment.to_dict() if assessment else {},
        "risks": list(watch.risks),
        "invalidation": list(watch.invalidation),
        "valid_until": recommendation.valid_until,
        "next_review_date": recommendation.next_review_date,
        "data_gaps": data_gaps,
    }


def _display_metrics(financial: Mapping[str, Any], quote: Quote | None) -> dict[str, Any]:
    metrics = dict(financial.get("display_metrics", {}))
    if quote is None:
        return metrics
    config = dict(financial["valuation"])
    eps_trade = _decimal(config["base_earnings_per_share"]) * _decimal(
        config.get("fx_to_trade_currency", "1")
    )
    dividend = _decimal(config.get("dividend_per_share_trade_currency", "0"))
    if eps_trade > 0:
        metrics["静态市盈率（估值基准 EPS）"] = f"{(quote.price / eps_trade).quantize(Decimal('0.01'))} 倍"
        metrics["盈利收益率（估值基准 EPS）"] = (
            f"{(eps_trade / quote.price * 100).quantize(Decimal('0.01'))}%"
        )
    if dividend >= 0:
        metrics["股息率（当前配置股息）"] = (
            f"{(dividend / quote.price * 100).quantize(Decimal('0.01'))}%"
        )
    return metrics


def _quote_mapping(quote: Quote, watch: WatchItem) -> dict[str, Any]:
    change_pct = None
    if quote.previous_close is not None and quote.previous_close > 0:
        change_pct = (quote.price / quote.previous_close - Decimal("1")) * Decimal("100")
    local_time = quote.observed_at.astimezone(ZoneInfo(watch.timezone))
    return {
        "value": decimal_to_str(quote.price),
        "currency": quote.currency,
        "change_pct": decimal_to_str(change_pct),
        "as_of": local_time.isoformat(),
        "source_url": quote.source_url,
        "provisional": quote.provisional,
        "freshness": quote.freshness.value,
        "provider": quote.provider,
        "previous_close": decimal_to_str(quote.previous_close),
        "open": decimal_to_str(quote.open),
        "high": decimal_to_str(quote.high),
        "low": decimal_to_str(quote.low),
        "bid": decimal_to_str(quote.bid),
        "ask": decimal_to_str(quote.ask),
        "turnover": decimal_to_str(quote.turnover),
        "volume": quote.volume,
    }


def _merge_sources(raw_sources: Iterable[Any], watch_sources: Iterable[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, Mapping):
            continue
        label, url = str(source.get("label", "来源")), str(source.get("url", ""))
        if url and url not in seen:
            result.append({"label": label, "url": url})
            seen.add(url)
    for source in watch_sources:
        if source.url not in seen:
            result.append({"label": source.label, "url": source.url})
            seen.add(source.url)
    return result


def _fundamental_period_fresh(period: str, period_type: str, now: datetime) -> bool:
    try:
        period_date = date.fromisoformat(period)
    except ValueError:
        return False
    max_days = 300 if period_type == "quarter" else 550
    age = now.date() - period_date
    return timedelta(0) <= age <= timedelta(days=max_days)


def _translate_rationale(text: str) -> str:
    translations = {
        "the thesis and quality gates passed": "投资逻辑与数据质量门槛已通过。",
        "the required safety margin or target annual return is not yet available at this price": "当前价格尚未同时满足安全边际与目标年化回报门槛。",
        "the thesis and quality gates passed with no red hard-risk override": "投资逻辑与质量门槛通过，且未触发红色硬风险。",
        "the price or return does not meet the stricter add/buy threshold": "当前价格或回报未达到更严格的新增风险敞口门槛。",
    }
    return translations.get(text, text)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("expected decimal value")
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError("decimal must be finite")
    return converted


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else _decimal(value)
