# tools/nse_history.py
"""NSE history store for capturing daily snapshots and computing technical indicators."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import afrimarket as afm
import pandas as pd

HISTORY_DIR = Path("data/nse_history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


class NSEHistoryStore:
    """Store and manage NSE historical data for technical analysis."""

    def __init__(self):
        self.exchange = afm.Exchange(market=afm.markets['Nairobi Securities Exchange'])
        # Get all NSE tickers, filtering out any invalid ones
        tickers_df = self.exchange.get_listed_companies()
        if tickers_df is None or tickers_df.empty:
            # Fallback to empty list and warn
            self.tickers = []
            print("Warning: Could not retrieve NSE ticker list, using empty universe")
        else:
            self.tickers = [
                ticker for ticker in tickers_df['Ticker'].tolist()
                if isinstance(ticker, str) and ticker.strip()
            ]
        print(f"NSEHistoryStore initialized with {len(self.tickers)} tickers")

    def capture_snapshot(self):
        """Fetch today's quotes and append to each ticker's CSV."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f"Capturing NSE snapshot for {today}")

        success_count = 0
        for ticker in self.tickers:
            try:
                stock = afm.Stock(ticker=ticker, market=afm.markets['Nairobi Securities Exchange'])
                price_data = stock.get_price()

                if price_data is None or price_data.empty:
                    print(f"No price data for {ticker}")
                    continue

                # Get the latest price (last row)
                latest_price = float(price_data.iloc[-1]['Price'])

                path = HISTORY_DIR / f"{ticker}.csv"

                # Read existing data or create new DataFrame
                if path.exists():
                    df = pd.read_csv(path)
                    # Avoid duplicate entries for the same day
                    if not df.empty and today in df["date"].values:
                        continue
                else:
                    df = pd.DataFrame(columns=["date", "price"])

                # Append today's data
                new_row = pd.DataFrame({"date": [today], "price": [latest_price]})
                df = pd.concat([df, new_row], ignore_index=True)
                df.to_csv(path, index=False)
                success_count += 1

            except Exception as e:  # noqa: BLE001
                # Log error but continue with other tickers
                print(f"Error processing {ticker}: {e}")
                continue

        print(f"Snapshot captured for {success_count}/{len(self.tickers)} tickers")
        return success_count

    def compute_indicators(self, ticker: str) -> dict:
        """Compute technical indicators for a ticker from its historical data."""
        path = HISTORY_DIR / f"{ticker}.csv"
        if not path.exists():
            return {}

        try:
            df = pd.read_csv(path).sort_values("date")
            if len(df) < 20:  # Need at least 20 days for SMA20
                return {"price": df["price"].iloc[-1] if not df.empty else 0, "bars": len(df)}

            close = df["price"]

            # Calculate SMA-20 and SMA-50
            sma20 = close.rolling(20).mean().iloc[-1]
            sma50 = close.rolling(50).mean().iloc[-1] if len(df) >= 50 else None

            # Calculate RSI-14
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi = (100 - (100 / (1 + rs))).iloc[-1]

            return {
                "price": close.iloc[-1],
                "sma20": float(sma20) if not pd.isna(sma20) else None,
                "sma50": float(sma50) if sma50 is not None and not pd.isna(sma50) else None,
                "rsi": float(rsi) if not pd.isna(rsi) else None,
                "bars": len(df),
            }
        except Exception as e:  # noqa: BLE001
            print(f"Error computing indicators for {ticker}: {e}")
            return {}


def main():
    """Test the NSE history store."""
    store = NSEHistoryStore()

    # Capture today's snapshot
    store.capture_snapshot()

    # Compute indicators for a few sample tickers
    sample_tickers = store.tickers[:5] if store.tickers else []
    for ticker in sample_tickers:
        indicators = store.compute_indicators(ticker)
        print(f"{ticker}: {indicators}")


if __name__ == "__main__":
    main()
