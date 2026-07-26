# data/yahoo_provider.py
import pandas as pd
import yfinance as yf

from data.provider import DataProvider
from utils.logger import log


class YahooFinanceProvider(DataProvider):
    def get_historical_data(
        self, symbol: str, start_date: str, end_date: str, interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetches historical data from Yahoo Finance."""
        log.info(f"Fetching {symbol} data from {start_date} to {end_date}")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval=interval)
            if df.empty:
                log.warning(f"No data found for {symbol}")
                return pd.DataFrame()
            # Clean and standardize columns
            df.columns = [col.lower().replace(" ", "_") for col in df.columns]
            df.rename(
                columns={"dividends": "dividend", "stock_splits": "split"}, inplace=True
            )
            return df
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to fetch data for {symbol}: {e}")
            return pd.DataFrame()

    def get_realtime_quote(self, symbol: str) -> dict:
        """Fetches real-time quote. (Simplified)"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            return {
                "symbol": symbol,
                "bid": info.get("bid", 0.0),
                "ask": info.get("ask", 0.0),
                "last": info.get("lastPrice", 0.0),
                "volume": info.get("lastVolume", 0),
            }
        except Exception as e:  # noqa: BLE001
            log.error(f"Failed to fetch quote for {symbol}: {e}")
            return {}
