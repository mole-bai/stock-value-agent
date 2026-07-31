"""Canonical identifiers and provider mappings for the first watchlist."""

from __future__ import annotations

from .models import Security


TENCENT = Security(
    ticker="0700.HK",
    market="HK",
    exchange="HKEX",
    issuer_id="HKEX:00700",
    name="腾讯控股",
)

POP_MART = Security(
    ticker="9992.HK",
    market="HK",
    exchange="HKEX",
    issuer_id="HKEX:09992",
    name="泡泡玛特",
)

KWEICHOW_MOUTAI = Security(
    ticker="600519.SS",
    market="CN",
    exchange="SSE",
    issuer_id="SSE:600519",
    name="贵州茅台",
)

WATCHLIST: tuple[Security, ...] = (TENCENT, POP_MART, KWEICHOW_MOUTAI)
WATCHLIST_BY_TICKER = {security.ticker: security for security in WATCHLIST}
