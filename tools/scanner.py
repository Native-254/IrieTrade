# tools/scanner.py
import logging

import pandas as pd
import yaml
import yfinance as yf

logger = logging.getLogger(__name__)


def find_volatile_candidates():
    """Screen a fixed list of high‑interest symbols for volatility."""
    candidates = [
        "QBTS", "RKLB", "SENS", "BNGO", "OPTT", "IDEX", "SMCI", "PLTR",
        "NVDA", "AVGO", "GEV", "CEG", "COIN", "MARA"
    ]
    results = []
    for sym in candidates:
        try:
            ticker = yf.Ticker(sym)
            info = ticker.info
            hist = ticker.history(period="3mo")
            if hist.empty:
                continue

            # Price > $1, daily volume > 500k
            if info.get("regularMarketPrice", 0) < 1:
                continue
            if info.get("averageDailyVolume10Day", 0) < 500_000:
                continue

            # ATR as percentage of price
            high = hist['High']
            low = hist['Low']
            close = hist['Close']
            tr = pd.concat(
                [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
                axis=1
            ).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            atr_pct = atr / close.iloc[-1]
            if atr_pct >= 0.02:   # at least 2% daily ATR
                results.append(sym)
                logger.info("%s: price=%.2f, ATR%%=%.2f%%", sym, close.iloc[-1], atr_pct * 100)
        except (KeyError, IndexError, ValueError, ConnectionError):
            logger.warning("Skipping %s due to data error", sym)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    volatile_list = find_volatile_candidates()
    with open("config/volatile_symbols.yaml", "w") as f:
        yaml.dump({"volatile_symbols": volatile_list}, f)
    print(f"Saved {len(volatile_list)} volatile symbols.")