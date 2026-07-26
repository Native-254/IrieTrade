# data/manager.py
import time
from pathlib import Path

import pandas as pd

from data.yahoo_provider import YahooFinanceProvider
from utils.config import CONFIG
from utils.logger import log


class DataManager:
    def __init__(self):
        self.providers = {}
        self.cache_dir = Path('data/raw')
        self.cache_dir.mkdir(exist_ok=True)

        if CONFIG['data']['sources'][0]['enabled']:
            self.providers['yahoo'] = YahooFinanceProvider()

    def get_data(self, symbol: str, start_date: str, end_date: str, interval: str = "1d", force_refresh: bool = False) -> pd.DataFrame:
        """Main method to fetch data, with caching."""
        cache_file = self.cache_dir / f"{symbol}_{start_date}_{end_date}_{interval}.parquet"

        if cache_file.exists() and not force_refresh:
            log.debug(f"Loading {symbol} data from cache: {cache_file}")
            return pd.read_parquet(cache_file)

        log.info(f"Fetching {symbol} data from providers...")
        df = pd.DataFrame()
        for name, provider in self.providers.items():
            try:
                df = provider.get_historical_data(symbol, start_date, end_date, interval)
                if not df.empty:
                    log.success(f"Successfully fetched {symbol} data from {name}")
                    if name == 'yahoo':
                        time.sleep(0.5)
                    break
            except Exception as e:  # noqa: BLE001
                log.warning(f"Provider {name} failed for {symbol}: {e}")

        if not df.empty:
            df.to_parquet(cache_file)
            log.debug(f"Cached data to {cache_file}")
            return df
        else:
            log.error(f"All providers failed to fetch data for {symbol}")
            return pd.DataFrame()

    def update_intraday_cache(self, symbols: list):
        """Periodically updates cache for real-time strategies."""
        log.info(f"Updating intraday cache for {len(symbols)} symbols...")
        for symbol in symbols:
            _ = self.get_data(symbol, start_date=(pd.Timestamp.now() - pd.Timedelta(days=7)).strftime('%Y-%m-%d'),
                              end_date=pd.Timestamp.now().strftime('%Y-%m-%d'), interval="5m", force_refresh=True)