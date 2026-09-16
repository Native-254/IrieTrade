# tools/nse_scanner.py
"""NSE market scanner using afrimarket for data."""

from __future__ import annotations

import afrimarket as afm


class NSEScanner:
    """Scans NSE market for trading candidates using afrimarket data."""

    def __init__(self, config: dict):
        self.config = config.get("nse", {})
        self.universe = self.config.get("universe", [
            "SCOM", "EQTY", "KCB", "EABL", "COOP",
            "ABSA", "NCBA", "KEGN", "KPLC", "SBIC",
            "DTK", "SCBK", "HFCK", "NMG", "JUB",
        ])
        self.top_n = self.config.get("top_n", 5)
        self._exchange = None

    @property
    def exchange(self):
        """Lazy initialization of NSE exchange."""
        if self._exchange is None:
            self._exchange = afm.Exchange(market=afm.markets['Nairobi Securities Exchange'])
        return self._exchange

    def scan(self) -> list[dict]:
        """Return ranked list of NSE candidates with quote + momentum data."""
        results = []

        for ticker in self.universe:
            try:
                # Get stock data using afrimarket
                stock = afm.Stock(ticker=ticker, market=afm.markets['Nairobi Securities Exchange'])
                price_data = stock.get_price()

                if price_data is None or price_data.empty or len(price_data) < 30:
                    continue

                # Extract close prices
                close_series = price_data['Price']

                # Note: afrimarket doesn't seem to provide volume data reliably
                # We'll work with price data only for now and use placeholder for volume ratio

                last = float(close_series.iloc[-1])

                # Calculate SMAs
                sma20 = float(close_series.rolling(20).mean().iloc[-1])
                sma50 = float(close_series.rolling(50).mean().iloc[-1]) if len(close_series) >= 50 else sma20

                # Calculate average volume (placeholder since volume data is unreliable in afrimarket)
                avg_vol_20 = 1.0  # Placeholder
                last_vol = 1.0    # Placeholder
                vol_ratio = last_vol / avg_vol_20 if avg_vol_20 > 0 else 0

                # Calculate price changes
                chg_5d = (last / float(close_series.iloc[-6]) - 1) * 100 if len(close_series) > 6 else 0
                chg_20d = (last / float(close_series.iloc[-21]) - 1) * 100 if len(close_series) > 21 else 0

                # Basic momentum score
                score = 0
                if last > sma20: score += 1
                if sma20 > sma50: score += 1
                if chg_5d > 0: score += 1
                # Skip vol_ratio check since we don't have good volume data
                if chg_20d > 3: score += 1

                results.append({
                    "ticker": ticker,
                    "price": last,
                    "sma20": sma20,
                    "sma50": sma50,
                    "chg_5d": chg_5d,
                    "chg_20d": chg_20d,
                    "vol_ratio": vol_ratio,  # Placeholder
                    "score": score,
                })
            except Exception as e:  # noqa: BLE001
                # Log error but continue scanning other tickers
                print(f"Error scanning {ticker}: {e}")
                continue

        # Sort by score and 5-day change
        results.sort(key=lambda r: (r["score"], r["chg_5d"]), reverse=True)
        return results[: self.top_n]

    def get_detailed_data(self, ticker: str) -> dict | None:
        """Get detailed data for a specific ticker including indicators from history store."""
        try:
            from .nse_history import NSEHistoryStore
            history_store = NSEHistoryStore()

            # Get current price data
            stock = afm.Stock(ticker=ticker, market=afm.markets['Nairobi Securities Exchange'])
            price_data = stock.get_price()

            if price_data is None or price_data.empty:
                return None

            last_price = float(price_data.iloc[-1]['Price'])

            # Get computed indicators from history
            indicators = history_store.compute_indicators(ticker)

            return {
                "ticker": ticker,
                "price": last_price,
                "sma20": indicators.get("sma20"),
                "sma50": indicators.get("sma50"),
                "rsi": indicators.get("rsi"),
                "bars": indicators.get("bars", 0),
            }
        except Exception as e:  # noqa: BLE001
            print(f"Error getting detailed data for {ticker}: {e}")
            return None
