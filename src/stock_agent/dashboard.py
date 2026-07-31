"""Build the deliberately small public payload consumed by the web dashboard."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class DashboardDataError(ValueError):
    """Raised when a report cannot be converted into public dashboard data."""


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DashboardDataError(f"{field} must be an object")
    return value


def _list(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise DashboardDataError(f"{field} must be a list")
    return value


def _pick(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields}


def public_dashboard_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return only fields that the public, read-only dashboard needs."""

    stocks = _list(report.get("stocks"), field="stocks")
    public_stocks: list[dict[str, Any]] = []
    for index, raw_stock in enumerate(stocks):
        stock = _mapping(raw_stock, field=f"stocks[{index}]")
        price = _mapping(stock.get("price"), field=f"stocks[{index}].price")
        recommendation = _mapping(
            stock.get("recommendation"), field=f"stocks[{index}].recommendation"
        )
        valuation = _mapping(
            recommendation.get("valuation"),
            field=f"stocks[{index}].recommendation.valuation",
        )
        public_stocks.append(
            {
                **_pick(stock, ("symbol", "name", "market")),
                "price": _pick(
                    price,
                    (
                        "as_of",
                        "change_pct",
                        "currency",
                        "freshness",
                        "provisional",
                        "source_url",
                        "value",
                    ),
                ),
                "metrics": dict(_mapping(stock.get("metrics", {}), field="metrics")),
                "signals": [
                    _pick(
                        _mapping(signal, field="signal"),
                        ("detail", "evidence_url", "severity", "title"),
                    )
                    for signal in _list(stock.get("signals", []), field="signals")
                ],
                "recommendation": {
                    **_pick(
                        recommendation,
                        (
                            "action",
                            "action_code",
                            "confidence",
                            "next_review_date",
                            "reasons",
                            "risks",
                            "invalidation",
                        ),
                    ),
                    "valuation": dict(valuation),
                },
            }
        )

    return {
        "schema_version": report.get("schema_version"),
        "run_at": report.get("run_at"),
        "status": report.get("status"),
        "warnings": list(_list(report.get("warnings", []), field="warnings")),
        "upcoming_events": list(
            _list(report.get("upcoming_events", []), field="upcoming_events")
        ),
        "stocks": public_stocks,
    }


def write_public_dashboard(source: Path, destination: Path) -> None:
    with source.open("r", encoding="utf-8") as handle:
        report = _mapping(json.load(handle), field="report")
    payload = public_dashboard_payload(report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="full daily JSON report")
    parser.add_argument("destination", type=Path, help="public dashboard JSON path")
    args = parser.parse_args()
    write_public_dashboard(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
