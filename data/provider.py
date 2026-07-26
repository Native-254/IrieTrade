# data/provider.py
from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    """Abstract base class for all data providers."""

    @abstractmethod
    def get_historical_data(
        self, symbol: str, start_date: str, end_date: str, interval: str = "1d"
    ) -> pd.DataFrame:
        """Fetches historical OHLCV data."""
        ...

    @abstractmethod
    def get_realtime_quote(self, symbol: str) -> dict:
        """Fetches real-time quote (bid/ask/last)."""
        ...
