# tools/scanner.py
from __future__ import annotations

import pandas as pd
import yfinance as yf

from utils.config import CONFIG
from utils.logger import log


class MarketScanner:
    """Screens universes for stocks and crypto based on technical criteria."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or CONFIG.get("scanner", {})
        self.universe = self.config.get("universe", [])
        self.criteria = self.config.get("criteria", {})
        self.top_n = self.config.get("top_n", 10)

    # ----------------------------------------------------------------
    # Stock scanning
    # ----------------------------------------------------------------
    def _load_stock_universe(self) -> list[str]:
        """Return the stock universe to scan. If no custom list, fetch S&P 500."""
        if self.universe:
            return self.universe
        try:
            table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
            tickers = table["Symbol"].tolist()
            log.info(f"Loaded {len(tickers)} stocks from S&P 500 list.")
            return tickers
        except Exception as e:  # noqa: BLE001
            log.warning(f"Could not fetch S&P 500 list: {e}. Falling back to IB symbol list.")
            return CONFIG["trading"]["symbols"]

    def fetch_historical_data(self, symbol: str, period: str = "3mo") -> pd.DataFrame | None:
        """Download historical data for a symbol."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            if df.empty:
                return None
            return df
        except Exception as e:  # noqa: BLE001
            log.debug(f"Data fetch failed for {symbol}: {e}")
            return None

    def calculate_indicators(self, df: pd.DataFrame) -> dict[str, float]:
        """Compute technical indicators used for screening."""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        last_price = close.iloc[-1]

        sma50 = close.rolling(window=50).mean().iloc[-1]
        sma200 = close.rolling(window=200).mean().iloc[-1]

        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))

        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=14).mean().iloc[-1]

        avg_volume = volume.rolling(window=50).mean().iloc[-1]

        trend_score = (close.iloc[-1] / sma50 - 1) * 100
        volatility_ratio = atr / last_price if last_price else 0.0

        return {
            "price": last_price,
            "sma50": sma50,
            "sma200": sma200,
            "rsi": rsi,
            "atr": atr,
            "avg_volume": avg_volume,
            "trend_score": trend_score,
            "volatility_ratio": volatility_ratio,
        }

    def passes_criteria(self, indicators: dict[str, float], symbol: str) -> bool:
        """Check if a symbol meets the configured screening criteria."""
        c = self.criteria
        if not c:
            return True

        min_price = c.get("min_price", 0)
        max_price = c.get("max_price", float("inf"))
        if not (min_price <= indicators["price"] <= max_price):
            return False

        min_volume = c.get("min_avg_volume", 0)
        if indicators["avg_volume"] < min_volume:
            return False

        if c.get("above_sma50", False) and indicators["price"] <= indicators["sma50"]:
            return False

        rsi_min = c.get("rsi_min", 0)
        rsi_max = c.get("rsi_max", 100)
        if not (rsi_min <= indicators["rsi"] <= rsi_max):
            return False

        min_vol = c.get("min_volatility_ratio", 0)
        max_vol = c.get("max_volatility_ratio", float("inf"))
        return min_vol <= indicators["volatility_ratio"] <= max_vol

    def rank_candidates(self, candidates: list[tuple[str, dict[str, float]]]) -> list[str]:
        """Rank candidates by a composite score (trend + RSI health)."""
        def score(item: tuple[str, dict[str, float]]) -> float:
            _, ind = item
            trend = ind["trend_score"]
            rsi = ind["rsi"]
            rsi_score = 1.0 - abs(rsi - 50) / 50.0
            return trend * 0.6 + rsi_score * 0.4

        candidates.sort(key=score, reverse=True)
        return [sym for sym, _ in candidates[: self.top_n]]

    def scan_stocks(self) -> list[str]:
        """Run the stock scanner and return a ranked list of symbols."""
        universe = self._load_stock_universe()
        candidates = []
        for symbol in universe:
            df = self.fetch_historical_data(symbol)
            if df is None or len(df) < 200:
                continue
            indicators = self.calculate_indicators(df)
            if self.passes_criteria(indicators, symbol):
                candidates.append((symbol, indicators))
        if not candidates:
            log.warning("No stock candidates passed the screening criteria.")
            return []
        ranked = self.rank_candidates(candidates)
        log.info(f"Stock scanner found {len(ranked)} candidates: {', '.join(ranked[:5])}...")
        return ranked

    # ----------------------------------------------------------------
    # Crypto scanning
    # ----------------------------------------------------------------
    def scan_crypto(self, exchange_name: str = "kucoin") -> list[str]:
        """Fetch all USDT pairs from a ccxt exchange, filter, and rank."""
        try:
            import ccxt
        except ImportError:
            log.error("ccxt not installed – cannot scan crypto.")
            return []

        exchange_class = getattr(ccxt, exchange_name, None)
        if exchange_class is None:
            log.error(f"Exchange '{exchange_name}' not supported by ccxt.")
            return []

        xchg_cfg = CONFIG.get("exchanges", {}).get(exchange_name, {})
        params: dict = {"enableRateLimit": True}
        if xchg_cfg.get("testnet", True) and exchange_name == "kucoin":
            params["urls"] = {
                "api": {
                    "public": "https://openapi-sandbox.kucoin.com",
                    "private": "https://openapi-sandbox.kucoin.com",
                }
            }
        exchange = exchange_class(params)
        exchange.load_markets()

        markets = {
            sym: mkt
            for sym, mkt in exchange.markets.items()
            if mkt.get("quote") == "USDT" and mkt.get("active") and mkt.get("spot")
        }

        tickers = exchange.fetch_tickers()
        candidates = []
        for sym in markets:
            ticker = tickers.get(sym)
            if ticker is None:
                continue
            base_volume = ticker.get("baseVolume", 0)
            last_price = ticker.get("last", 0)
            if base_volume > 0 and last_price > 0:
                percent_change = ticker.get("percentage", 0) or 0
                candidates.append((sym, base_volume, last_price, percent_change))

        min_volume_usdt = self.criteria.get("min_volume_usdt", 0)
        filtered = [
            (sym, vol, price, chg)
            for sym, vol, price, chg in candidates
            if vol * price >= min_volume_usdt
        ]

        filtered.sort(key=lambda x: (x[1] * x[2], abs(x[3])), reverse=True)

        top_symbols = [sym for sym, _, _, _ in filtered[: self.top_n]]
        log.info(f"Crypto scanner found {len(top_symbols)} pairs: {', '.join(top_symbols[:5])}...")
        return top_symbols