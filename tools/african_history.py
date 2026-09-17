"""Persistent price history and indicator computation for African exchanges.

Data sources, in order of preference:
    1. Mansa Markets API (mansaapi.com) — primary
    2. afx.kwayisi.org — fallback scraper

The store appends one row per symbol per day. Indicators are computed
on demand from the accumulated series.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from utils.logger import log

HISTORY_DIR = Path("data/african_history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


class AfricanHistoryStore:
    """Accumulates daily price snapshots and computes indicators."""

    EXCHANGES = ("NSE", "JSE", "NGX", "GSE", "BRVM")

    def __init__(self, scanner):
        """scanner: any object implementing get_quotes(exchange) -> list[dict]."""
        self.scanner = scanner

    def _path(self, exchange: str, symbol: str) -> Path:
        safe = symbol.replace("/", "_").replace(".", "_")
        return HISTORY_DIR / f"{exchange}_{safe}.csv"

    def capture_all(self) -> None:
        """Fetch today's quotes for every configured exchange and append."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for exchange in self.EXCHANGES:
            try:
                quotes = self.scanner.get_quotes(exchange)
            except Exception as e:  # noqa: BLE001
                log.warning(f"History capture: {exchange} fetch failed: {e}")
                continue

            if not quotes:
                log.warning(f"History capture: no quotes for {exchange}")
                continue

            appended = 0
            for q in quotes:
                sym = q.get("symbol")
                price = q.get("price")
                if not sym or price is None:
                    continue
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue

                path = self._path(exchange, sym)
                df = pd.read_csv(path) if path.exists() else pd.DataFrame()

                if not df.empty and today in df["date"].astype(str).values:
                    continue  # already captured today

                row = {
                    "date": today,
                    "price": price,
                    "volume": float(q.get("volume", 0) or 0),
                    "change_pct": float(q.get("change_pct", 0) or 0),
                }
                df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
                df.to_csv(path, index=False)
                appended += 1

            log.info(f"History capture: {exchange} appended {appended} symbols")

    def compute_indicators(self, exchange: str, symbol: str) -> dict:
        """Return SMA20, SMA50, RSI14 if enough history exists."""
        path = self._path(exchange, symbol)
        if not path.exists():
            return {}

        df = pd.read_csv(path).sort_values("date")
        if df.empty:
            return {}

        close = df["price"]
        result: dict = {
            "price": float(close.iloc[-1]),
            "bars": len(df),
        }

        if len(df) >= 20:
            result["sma20"] = float(close.rolling(20).mean().iloc[-1])
        if len(df) >= 50:
            result["sma50"] = float(close.rolling(50).mean().iloc[-1])
        if len(df) >= 15:
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, float("nan"))
            result["rsi"] = float((100 - (100 / (1 + rs))).iloc[-1])

        return result

    def all_symbols(self, exchange: str) -> list[str]:
        """Return every symbol we have history for on a given exchange."""
        prefix = f"{exchange}_"
        symbols = []
        for path in HISTORY_DIR.glob(f"{prefix}*.csv"):
            name = path.stem[len(prefix):]
            symbols.append(name.replace("_", "."))
        return symbols
