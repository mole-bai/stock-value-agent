"""Render a structured daily run result as a Chinese Markdown report.

The renderer deliberately contains no investment calculations.  It formats the
deterministic output produced by the upstream price, signal, valuation and
recommendation engines, and degrades visibly when a field is unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import html
from typing import Any
from urllib.parse import urlparse


DEFAULT_DISCLAIMER = (
    "本报告仅用于信息整理和研究，不构成投资建议、证券要约或收益保证。"
    "估值结果依赖输入数据与假设，可能随新信息显著变化；请核对原始披露，"
    "并结合个人目标、风险承受能力和财务状况独立决策。"
)

_MISSING = "未提供"
_SEVERITY_LABELS = {
    "critical": "红色/严重",
    "red": "红色/严重",
    "high": "橙色/高",
    "orange": "橙色/高",
    "medium": "黄色/中",
    "yellow": "黄色/中",
    "low": "蓝色/低",
    "info": "信息",
    "information": "信息",
}
_STATUS_LABELS = {
    "success": "完整",
    "succeeded": "完整",
    "complete": "完整",
    "completed": "完整",
    "ok": "完整",
    "partial": "部分降级",
    "degraded": "部分降级",
    "partial_degraded": "部分降级",
    "failed": "失败",
    "failure": "失败",
    "error": "失败",
    "running": "运行中",
}
_SCENARIOS = (("bear", "悲观"), ("base", "基准"), ("bull", "乐观"))


def _mapping(value: Any) -> dict[str, Any]:
    """Convert common structured-result objects into a shallow dictionary."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    try:
        return dict(vars(value))
    except (TypeError, ValueError):
        return {}


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        return []
    if isinstance(value, Iterable):
        return list(value)
    return []


def _inline(value: Any, *, missing: str = _MISSING) -> str:
    """Escape an untrusted scalar for use in regular Markdown text."""

    if value is None or value == "":
        return missing
    if isinstance(value, bool):
        text = "是" if value else "否"
    elif isinstance(value, (datetime, date)):
        text = value.isoformat()
    else:
        text = str(value)
    text = " ".join(text.split())
    text = html.escape(text, quote=False)
    for char in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(char, f"\\{char}")
    return text or missing


def _table(value: Any, *, missing: str = _MISSING) -> str:
    return _inline(value, missing=missing).replace("|", "\\|")


def _format_number(value: Any, *, decimals: int = 2) -> str:
    if value is None or value == "":
        return _MISSING
    if isinstance(value, bool):
        return _inline(value)
    if isinstance(value, str):
        try:
            value = Decimal(value.strip())
        except Exception:
            return _inline(value)
    if isinstance(value, (int, float, Decimal)):
        return f"{value:,.{decimals}f}"
    return _inline(value)


def _format_percent(
    value: Any, *, fraction: bool = False, show_sign: bool = False
) -> str:
    if value is None or value == "":
        return _MISSING
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            return _inline(text)
        try:
            value = Decimal(text)
        except Exception:  # display, rather than reject, upstream labels such as N/M
            return _inline(text)
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        numeric = Decimal(str(value))
        if fraction:
            numeric *= 100
        sign = "+" if show_sign and numeric > 0 else ""
        return f"{sign}{numeric:.2f}%"
    return _inline(value)


def _format_money(value: Any, currency: Any = None) -> str:
    data = _mapping(value)
    if data:
        currency = data.get("currency", currency)
        if data.get("low") is not None or data.get("high") is not None:
            low = _format_number(data.get("low"))
            high = _format_number(data.get("high"))
            amount = f"{low}–{high}"
        else:
            amount = _format_number(
                data.get(
                    "value",
                    data.get(
                        "intrinsic_value",
                        data.get("intrinsic_value_per_share", data.get("amount")),
                    ),
                )
            )
    else:
        amount = _format_number(value)
    if amount == _MISSING:
        return amount
    suffix = f" {_inline(currency)}" if currency not in (None, "") else ""
    return f"{amount}{suffix}"


def _safe_link(label: Any, url: Any) -> str | None:
    if not url:
        return None
    raw_url = str(url).strip()
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    escaped_url = raw_url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"[{_inline(label, missing='来源')}]({escaped_url})"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_inline(value)] if value.strip() else []
    result: list[str] = []
    for item in _items(value):
        data = _mapping(item)
        if data:
            text = data.get("detail", data.get("title", data.get("name", data.get("value"))))
        else:
            text = item
        if text not in (None, ""):
            result.append(_inline(text))
    return result


def _format_metric_value(value: Any) -> str:
    data = _mapping(value)
    if not data:
        return _inline(value)
    raw = data.get("value", data.get("current", data.get("amount")))
    if raw is None:
        return _inline(value)
    unit = data.get("unit")
    if unit == "%" or data.get("format") == "percent":
        formatted = _format_percent(raw)
    elif data.get("currency"):
        formatted = _format_money(raw, data.get("currency"))
    else:
        formatted = _format_number(raw)
        if unit:
            formatted = f"{formatted} {_inline(unit)}"
    period = data.get("period", data.get("as_of"))
    return f"{formatted}（{_inline(period)}）" if period else formatted


def _status(value: Any) -> str:
    if value is None or value == "":
        return "未知（状态缺失）"
    key = str(value).strip().lower()
    return _STATUS_LABELS.get(key, _inline(value))


def _data_cutoff(run: Mapping[str, Any], stocks: list[Any]) -> str:
    explicit = run.get("data_as_of", run.get("as_of"))
    if explicit:
        return _inline(explicit)
    cutoffs: list[str] = []
    for stock_value in stocks:
        stock = _mapping(stock_value)
        price = _mapping(stock.get("price"))
        as_of = price.get("as_of")
        if as_of:
            symbol = stock.get("symbol", stock.get("name", "股票"))
            cutoffs.append(f"{_inline(symbol)}：{_inline(as_of)}")
    if not cutoffs:
        return "未提供（报告中的时效性结论需谨慎使用）"
    return "；".join(cutoffs)


def _top_item(stock: Mapping[str, Any]) -> str:
    symbol = _inline(stock.get("symbol", stock.get("name", "未知股票")))
    signals = _items(stock.get("signals"))
    if signals:
        signal = _mapping(signals[0])
        severity = _SEVERITY_LABELS.get(
            str(signal.get("severity", "")).lower(),
            _inline(signal.get("severity"), missing="未分级"),
        )
        return f"{symbol}：{_inline(signal.get('title'), missing='新增信号')}（{severity}）"
    events = _items(stock.get("events"))
    if events:
        return f"{symbol}：{_inline(_mapping(events[0]).get('title'), missing='新增事件')}"
    return f"{symbol}：未发现新增事件或信号"


class DailyMarkdownRenderer:
    """Compose a deterministic Chinese Markdown daily report."""

    def render(self, result: Any) -> str:
        run = _mapping(result)
        stocks = _items(run.get("stocks"))

        lines = [
            "# 自选股价值投资监控日报",
            "",
            f"- 数据截至：{_data_cutoff(run, stocks)}",
            f"- 运行时间：{_inline(run.get('run_at'))}",
            f"- 运行状态：{_status(run.get('status'))}",
            f"- 运行模式：{_inline(run.get('mode'), missing='daily')}",
        ]

        warnings = _string_list(run.get("warnings"))
        if warnings:
            lines.extend(["", "## 运行警告", ""])
            lines.extend(f"- {warning}" for warning in warnings)

        lines.extend(self._render_calendar(run))
        lines.extend(self._render_delta(run))
        lines.extend(self._render_pending_reviews(run))
        lines.extend(self._render_notifications(run))

        lines.extend(["", "## 今日摘要", ""])
        if stocks:
            lines.extend(
                f"{index}. {_top_item(_mapping(stock))}"
                for index, stock in enumerate(stocks[:3], start=1)
            )
        else:
            lines.append("暂无股票结果；本次运行未提供可生成卡片的数据。")

        lines.extend(["", f"## 股票卡片（{len(stocks)} 只）", ""])
        if stocks:
            for index, stock in enumerate(stocks, start=1):
                lines.extend(self._render_stock(index, _mapping(stock)))
        else:
            lines.append("暂无。")

        disclaimers = _string_list(run.get("disclaimers", run.get("disclaimer")))
        lines.extend(["", "## 免责声明", "", DEFAULT_DISCLAIMER])
        lines.extend(f"\n- {item}" for item in disclaimers if item != DEFAULT_DISCLAIMER)
        return "\n".join(lines).rstrip() + "\n"

    def _render_calendar(self, run: Mapping[str, Any]) -> list[str]:
        events = _items(run.get("upcoming_events"))
        reminders = _items(run.get("calendar_reminders"))
        if not events:
            return []
        lines = ["", "## 未来 30 天投资者事件", ""]
        reminder_ids = {
            _mapping(item).get("event", {}).get("event_id")
            for item in reminders
            if isinstance(_mapping(item).get("event"), Mapping)
        }
        for raw_event in events:
            event = _mapping(raw_event)
            confidence = str(event.get("confidence", ""))
            confidence_label = {
                "official_confirmed": "官方确认",
                "vendor_expected": "供应商预计",
                "inferred": "推测，待确认",
            }.get(confidence, confidence or "置信度缺失")
            due = "；今日提醒" if event.get("event_id") in reminder_ids else ""
            link = _safe_link("来源", event.get("source_url"))
            suffix = f"；{link}" if link else ""
            lines.append(
                f"- {_inline(event.get('symbol'))}｜{_inline(event.get('title'))}："
                f"{_inline(event.get('start'))}（{_inline(confidence_label)}{due}）{suffix}"
            )
        return lines

    def _render_delta(self, run: Mapping[str, Any]) -> list[str]:
        delta = _mapping(run.get("delta"))
        if not delta:
            return []
        if delta.get("baseline"):
            return [
                "",
                "## 相比上次",
                "",
                "- 这是首个 point-in-time 基线；本轮不把已有事实误报成新增变化。",
            ]
        summary = _mapping(delta.get("summary"))
        lines = [
            "",
            "## 相比上次",
            "",
            f"- 发生实质变化的股票：{_inline(summary.get('changed_stock_count'), missing='0')} / "
            f"{_inline(summary.get('stock_count'), missing='0')}",
            f"- 建议或估值中枢变化：{_inline(summary.get('recommendation_changes'), missing='0')}；"
            f"事件变化：{_inline(summary.get('event_changes'), missing='0')}；"
            f"指标变化：{_inline(summary.get('metric_changes'), missing='0')}",
        ]
        reason_labels = {
            "price": "价格",
            "new_fundamental": "新财务事实",
            "new_event": "新事件/信号",
            "assumption": "估值假设",
            "rule": "规则",
            "data_quality": "数据质量",
        }
        for raw_stock in _items(delta.get("stocks")):
            stock = _mapping(raw_stock)
            if not stock.get("has_changes"):
                continue
            symbol = _inline(stock.get("symbol"))
            parts: list[str] = []
            price = _mapping(stock.get("price"))
            if price.get("kind") == "changed":
                parts.append(
                    f"价格 {_inline(price.get('previous'))} → {_inline(price.get('current'))}"
                    f"（{_format_percent(price.get('percent_change'), fraction=True, show_sign=True)}）"
                )
            recommendation = _mapping(stock.get("recommendation"))
            action = _mapping(recommendation.get("action"))
            if action.get("kind") == "changed":
                parts.append(
                    f"建议 {_inline(action.get('previous'))} → {_inline(action.get('current'))}"
                )
            signal_count = len(_items(stock.get("signals")))
            event_count = len(_items(stock.get("events")))
            if signal_count:
                parts.append(f"信号变化 {signal_count} 项")
            if event_count:
                parts.append(f"事件变化 {event_count} 项")
            reasons = [
                reason_labels.get(str(reason), str(reason))
                for reason in _items(stock.get("reason_codes"))
            ]
            reason_text = f"；归因：{'、'.join(reasons)}" if reasons else ""
            lines.append(f"- {symbol}：{'；'.join(parts) or '结构化内容变化'}{reason_text}")
        return lines

    def _render_pending_reviews(self, run: Mapping[str, Any]) -> list[str]:
        reviews = _items(run.get("pending_reviews"))
        if not reviews:
            return []
        lines = ["", "## 待人工复核", ""]
        for raw in reviews:
            item = _mapping(raw)
            link = _safe_link("原文", item.get("source_url"))
            suffix = f"；{link}" if link else ""
            status_label = {
                "pending": "待语义复核",
                "action_required": "需更新数据并重估",
            }.get(str(item.get("status")), str(item.get("status") or "待复核"))
            lines.append(
                f"- {_inline(item.get('review_id'))}｜{_inline(item.get('title'))}"
                f"（{_inline(status_label)}）{suffix}"
            )
        lines.append("- 未完成复核的官方文件会冻结相应股票的正面研究观点。")
        return lines

    def _render_notifications(self, run: Mapping[str, Any]) -> list[str]:
        notifications = _items(run.get("notifications"))
        if not notifications:
            return []
        sent = sum(bool(_mapping(_mapping(item).get("delivery")).get("sent")) for item in notifications)
        deferred = sum(bool(_mapping(_mapping(item).get("delivery")).get("deferred")) for item in notifications)
        return [
            "",
            "## 本轮通知",
            "",
            f"- 生成 {len(notifications)} 条；已写入本地通知箱 {sent} 条；静默时段延后 {deferred} 条。",
        ]

    def _render_stock(self, index: int, stock: Mapping[str, Any]) -> list[str]:
        symbol = _inline(stock.get("symbol"), missing="未知代码")
        name = _inline(stock.get("name"), missing="未知公司")
        market = _inline(stock.get("market"), missing="未知市场")
        lines = [f"### {index}. {symbol}｜{name}（{market}）", ""]
        lines.extend(self._render_price(stock))
        lines.extend(self._render_data_status(stock))
        lines.extend(self._render_metrics(stock))
        lines.extend(self._render_events(stock))
        lines.extend(self._render_signals(stock))
        lines.extend(self._render_recommendation(stock))
        lines.extend(self._render_sources(stock))
        return lines

    def _render_price(self, stock: Mapping[str, Any]) -> list[str]:
        price = _mapping(stock.get("price"))
        currency = price.get("currency")
        provisional = "（临时价格，待供应商校正）" if price.get("provisional") else ""
        source = _safe_link("价格来源", price.get("source_url"))
        lines = [
            "#### 价格变化",
            "",
            f"- 最新价格：{_format_money(price.get('value'), currency)}{provisional}",
            f"- 当日变化：{_format_percent(price.get('change_pct'), show_sign=True)}",
            f"- 价格时间：{_inline(price.get('as_of'))}",
        ]
        if source:
            lines.append(f"- {source}")
        return lines + [""]

    def _render_data_status(self, stock: Mapping[str, Any]) -> list[str]:
        status = _mapping(stock.get("data_status"))
        if not status:
            return []
        monitor = {
            "semantic": "公告标题/文档 ID 语义监控",
            "legacy_byte": "旧版网页字节监控",
            "disabled": "未启用",
        }.get(str(status.get("announcement_monitor")), _inline(status.get("announcement_monitor")))
        fresh = "是" if status.get("financial_period_fresh") else "否"
        return [
            "#### 数据状态",
            "",
            f"- 财务报告期：{_inline(status.get('financial_period'))} "
            f"（{_inline(status.get('financial_period_type'))}；时效合格：{fresh}）",
            f"- 财务快照核验时间：{_inline(status.get('fundamentals_snapshot_at'))}",
            f"- 公告监控：{monitor}；成功提取 "
            f"{_inline(status.get('announcement_sources_extracted'), missing='0')} / "
            f"{_inline(status.get('announcement_sources_total'), missing='0')} 个入口；"
            f"待复核 {_inline(status.get('pending_review_count'), missing='0')} 条",
            f"- 估值模型：{_inline(status.get('valuation_model'))}；"
            f"官方财务来源 {_inline(status.get('official_source_count'), missing='0')} 个",
            "",
        ]

    def _render_metrics(self, stock: Mapping[str, Any]) -> list[str]:
        metrics = _mapping(stock.get("metrics"))
        if not metrics:
            return []
        lines = ["#### 关键指标", "", "| 指标 | 最新值 |", "|---|---:|"]
        for name, value in metrics.items():
            lines.append(f"| {_table(name)} | {_table(_format_metric_value(value))} |")
        return lines + [""]

    def _render_events(self, stock: Mapping[str, Any]) -> list[str]:
        events = _items(stock.get("events"))
        if not events:
            return []
        lines = ["#### 最新事件", ""]
        for value in events:
            event = _mapping(value)
            title = _inline(event.get("title"), missing="未命名事件")
            published_at = _inline(event.get("published_at"))
            link = _safe_link("原文", event.get("source_url"))
            suffix = f"；{link}" if link else ""
            lines.append(f"- {title}（发布时间：{published_at}）{suffix}")
        return lines + [""]

    def _render_signals(self, stock: Mapping[str, Any]) -> list[str]:
        signals = _items(stock.get("signals"))
        lines = ["#### 财务与风险信号", ""]
        if not signals:
            lines.append("- 暂无新增信号。")
            return lines + [""]
        for value in signals:
            signal = _mapping(value)
            raw_severity = str(signal.get("severity", "")).lower()
            severity = _SEVERITY_LABELS.get(
                raw_severity, _inline(signal.get("severity"), missing="未分级")
            )
            title = _inline(signal.get("title"), missing="未命名信号")
            detail = _inline(signal.get("detail"), missing="未提供详情")
            evidence = _safe_link("证据", signal.get("evidence_url"))
            suffix = f"；{evidence}" if evidence else ""
            lines.append(f"- **{severity}｜{title}**：{detail}{suffix}")
        return lines + [""]

    def _render_recommendation(self, stock: Mapping[str, Any]) -> list[str]:
        recommendation = _mapping(stock.get("recommendation"))
        valuation = _mapping(recommendation.get("valuation"))
        currency = _mapping(stock.get("price")).get("currency")
        lines = [
            "#### 投资建议",
            "",
            f"- 行动倾向：{_inline(recommendation.get('action'), missing='无建议')}",
            f"- 建议范围：{_inline(recommendation.get('scope'), missing='公司级研究观点')}",
            f"- 置信度：{_inline(recommendation.get('confidence'), missing='数据不足')}",
        ]
        if recommendation.get("valid_until"):
            lines.append(f"- 有效期至：{_inline(recommendation.get('valid_until'))}")
        if recommendation.get("next_review_date"):
            lines.append(
                f"- 下次计划复核：{_inline(recommendation.get('next_review_date'))}"
            )

        reasons = _string_list(recommendation.get("reasons"))
        reason_codes = _string_list(recommendation.get("reason_codes"))
        lines.append("- 核心依据：")
        if reasons or reason_codes:
            lines.extend(f"  - {reason}" for reason in reasons)
            if reason_codes:
                lines.append(f"  - 规则代码：{'、'.join(reason_codes)}")
        else:
            lines.append("  - 未提供。")

        lines.extend(
            [
                "",
                "##### 三情景估值与安全边际",
                "",
                "| 情景 | 内在价值 | 安全边际 | 预期回报 | 核心假设 |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for key, label in _SCENARIOS:
            scenario = valuation.get(key)
            scenario_data = _mapping(scenario)
            intrinsic_value = _format_money(scenario, currency)
            margin = scenario_data.get("margin_of_safety")
            if margin is None and key == "base":
                margin = valuation.get("margin_of_safety")
            if isinstance(margin, Mapping):
                margin = margin.get(key)
            expected_return = scenario_data.get(
                "expected_return", scenario_data.get("expected_annual_return")
            )
            if expected_return is None:
                expected_return = valuation.get(f"expected_return_{key}")
            assumptions = scenario_data.get(
                "assumptions", scenario_data.get("key_assumptions")
            )
            if isinstance(assumptions, (list, tuple, set)):
                assumptions_text = "；".join(_string_list(assumptions)) or _MISSING
            else:
                assumptions_text = _inline(assumptions)
            lines.append(
                f"| {label} | {_table(intrinsic_value)} | "
                f"{_table(_format_percent(margin, fraction=True))} | "
                f"{_table(_format_percent(expected_return, fraction=True, show_sign=True))} | "
                f"{_table(assumptions_text)} |"
            )

        lines.extend(["", "##### 风险与投资逻辑失效条件", ""])
        risks = _string_list(recommendation.get("risks", stock.get("risks")))
        invalidation = _string_list(
            recommendation.get("invalidation", stock.get("invalidation"))
        )
        lines.append("- 主要风险：")
        lines.extend(f"  - {risk}" for risk in risks or ["未提供。"])
        lines.append("- 失效条件：")
        lines.extend(f"  - {condition}" for condition in invalidation or ["未提供。"])

        data_gaps = _string_list(
            recommendation.get("data_gaps", stock.get("data_gaps"))
        )
        if data_gaps:
            lines.append("- 数据缺口：")
            lines.extend(f"  - {gap}" for gap in data_gaps)
        return lines + [""]

    def _render_sources(self, stock: Mapping[str, Any]) -> list[str]:
        links: list[str] = []
        seen_urls: set[str] = set()

        def append_link(label: Any, url: Any) -> None:
            raw = str(url).strip() if url else ""
            if not raw or raw in seen_urls:
                return
            link = _safe_link(label, raw)
            if link:
                links.append(link)
                seen_urls.add(raw)

        for source_value in _items(stock.get("sources")):
            source = _mapping(source_value)
            append_link(source.get("label", "来源"), source.get("url"))
        price = _mapping(stock.get("price"))
        append_link("价格来源", price.get("source_url"))
        for signal_value in _items(stock.get("signals")):
            signal = _mapping(signal_value)
            append_link(signal.get("title", "信号证据"), signal.get("evidence_url"))
        for event_value in _items(stock.get("events")):
            event = _mapping(event_value)
            append_link(event.get("title", "事件原文"), event.get("source_url"))

        lines = ["#### 来源链接", ""]
        if links:
            lines.extend(f"- {link}" for link in links)
        else:
            lines.append("- 未提供可验证的来源链接。")
        return lines + [""]


def render_daily_markdown(result: Any) -> str:
    """Convenience function for rendering a daily-run result."""

    return DailyMarkdownRenderer().render(result)
