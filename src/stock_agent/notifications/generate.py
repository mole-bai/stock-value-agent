"""Translate deterministic run deltas into tiered notifications."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .models import Notification, NotificationPriority


def build_run_notifications(
    result: Mapping[str, Any], *, now: datetime
) -> tuple[Notification, ...]:
    """Build alerts only from structured, already-calculated run output."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    notices: list[Notification] = []
    delta = _mapping(result.get("delta"))
    if not delta.get("baseline"):
        stock_results = {
            str(stock.get("symbol")): stock
            for stock in _mappings(result.get("stocks"))
            if stock.get("symbol")
        }
        for stock_delta in _mappings(delta.get("stocks")):
            symbol = str(stock_delta.get("symbol", ""))
            if not symbol:
                continue
            current_stock = stock_results.get(symbol, {})
            notices.extend(
                _stock_delta_notifications(
                    stock_delta, current_stock=current_stock, now=now
                )
            )

    for raw in _mappings(result.get("pending_reviews")):
        symbol = str(raw.get("symbol", ""))
        review_id = str(raw.get("review_id", ""))
        if not symbol or not review_id:
            continue
        review_status = str(raw.get("status", "pending"))
        status_text = (
            "已确认需要更新数据并重估"
            if review_status == "action_required"
            else "等待语义复核"
        )
        notices.append(
            Notification(
                notification_id=f"review:{review_id}",
                dedupe_key=f"review:{review_id}",
                priority=NotificationPriority.P1,
                category="filing_review",
                title=f"{symbol} 有新官方文件待复核",
                body=f"{raw.get('title') or '新公告'}；状态：{status_text}。",
                created_at=now,
                symbol=symbol,
                source_url=_optional_http(raw.get("source_url")),
                attributes={"review_id": review_id, "status": review_status},
            )
        )

    for raw in _mappings(result.get("calendar_reminders")):
        event = _mapping(raw.get("event"))
        reminder_id = str(raw.get("reminder_id", ""))
        if not reminder_id or not event:
            continue
        days_before = int(raw.get("days_before", 0))
        confirmed = event.get("confidence") == "official_confirmed"
        priority = (
            NotificationPriority.P2
            if confirmed and days_before <= 1
            else NotificationPriority.P3
        )
        label = "今天" if days_before == 0 else f"{days_before} 天后"
        notices.append(
            Notification(
                notification_id=f"calendar:{reminder_id}",
                dedupe_key=f"calendar:{reminder_id}",
                priority=priority,
                category="investor_calendar",
                title=f"{event.get('symbol', '')} 投资者事件提醒",
                body=f"{label}：{event.get('title', '未命名事件')}；时间 {event.get('start', '未提供')}。",
                created_at=now,
                symbol=str(event.get("symbol")) if event.get("symbol") else None,
                source_url=_optional_http(event.get("source_url")),
                attributes={
                    "event_id": event.get("event_id"),
                    "confidence": event.get("confidence"),
                    "days_before": days_before,
                },
            )
        )
    # Multiple derivations may point to one logical event.  Stable ordering and
    # first-wins dedupe keep the outbox deterministic.
    unique: dict[str, Notification] = {}
    for notice in sorted(notices, key=lambda item: (int(item.priority), item.dedupe_key)):
        unique.setdefault(notice.dedupe_key, notice)
    return tuple(unique.values())


def _stock_delta_notifications(
    delta: Mapping[str, Any], *, current_stock: Mapping[str, Any], now: datetime
) -> list[Notification]:
    symbol = str(delta["symbol"])
    notices: list[Notification] = []
    recommendation = _mapping(delta.get("recommendation"))
    action = _mapping(recommendation.get("action"))
    if action.get("kind") == "changed":
        old_action = action.get("previous") or "未知"
        new_action = action.get("current") or "未知"
        priority = (
            NotificationPriority.P0
            if str(new_action) in {"risk_avoidance", "风险回避"}
            else NotificationPriority.P1
        )
        reasons = "、".join(str(item) for item in recommendation.get("reason_codes", []))
        notices.append(
            Notification(
                notification_id=f"recommendation:{symbol}:{new_action}",
                dedupe_key=f"recommendation:{symbol}",
                priority=priority,
                category="recommendation_change",
                title=f"{symbol} 研究观点发生变化",
                body=f"{old_action} → {new_action}" + (f"；归因：{reasons}" if reasons else ""),
                created_at=now,
                symbol=symbol,
                attributes={"previous": old_action, "current": new_action},
            )
        )

    for signal in _mappings(delta.get("signals")):
        if signal.get("transition") not in {"added", "upgraded"}:
            continue
        severity = str(signal.get("current_severity", "")).lower()
        priority = {
            "red": NotificationPriority.P0,
            "critical": NotificationPriority.P0,
            "orange": NotificationPriority.P1,
            "high": NotificationPriority.P1,
            "yellow": NotificationPriority.P2,
            "medium": NotificationPriority.P2,
        }.get(severity, NotificationPriority.P3)
        signal_id = str(signal.get("signal_id", signal.get("title", "signal")))
        notices.append(
            Notification(
                notification_id=f"signal:{symbol}:{signal_id}:{severity}",
                dedupe_key=f"signal:{symbol}:{signal_id}",
                priority=priority,
                category="financial_signal",
                title=f"{symbol} 财务信号{_transition_label(signal.get('transition'))}",
                body=f"{signal.get('title', signal_id)}；当前级别 {severity or '未分级'}。",
                created_at=now,
                symbol=symbol,
            )
        )

    price_change = _mapping(delta.get("price"))
    old_price = _decimal(price_change.get("previous"))
    new_price = _decimal(price_change.get("current"))
    audit_rec = _mapping(_mapping(current_stock.get("audit")).get("recommendation"))
    price_bands = _mapping(audit_rec.get("price_bands"))
    if old_price is not None and new_price is not None:
        entry = _decimal(price_bands.get("entry_price_ceiling"))
        expensive = _decimal(price_bands.get("expensive_price"))
        currency = _mapping(current_stock.get("price")).get("currency", "")
        if entry is not None and old_price > entry >= new_price:
            notices.append(
                _threshold_notice(
                    symbol=symbol,
                    name="entry",
                    title=f"{symbol} 价格进入建仓关注区间",
                    body=f"价格 {old_price} → {new_price} {currency}，建仓上限 {entry} {currency}；仍须同时检查回报门槛和风险信号。",
                    now=now,
                    values={"previous": str(old_price), "current": str(new_price), "threshold": str(entry)},
                )
            )
        if expensive is not None and old_price < expensive <= new_price:
            notices.append(
                _threshold_notice(
                    symbol=symbol,
                    name="expensive",
                    title=f"{symbol} 价格进入偏贵区间",
                    body=f"价格 {old_price} → {new_price} {currency}，偏贵阈值 {expensive} {currency}；需复核预期回报。",
                    now=now,
                    values={"previous": str(old_price), "current": str(new_price), "threshold": str(expensive)},
                )
            )
    return notices


def _threshold_notice(
    *,
    symbol: str,
    name: str,
    title: str,
    body: str,
    now: datetime,
    values: Mapping[str, Any],
) -> Notification:
    return Notification(
        notification_id=f"threshold:{symbol}:{name}",
        dedupe_key=f"threshold:{symbol}:{name}",
        priority=NotificationPriority.P2,
        category="valuation_threshold",
        title=title,
        body=body,
        created_at=now,
        symbol=symbol,
        attributes=values,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _optional_http(value: Any) -> str | None:
    text = str(value or "")
    return text if text.startswith(("https://", "http://")) else None


def _transition_label(value: Any) -> str:
    return "升级" if value == "upgraded" else "新增"
