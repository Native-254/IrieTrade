# strategies/vwap_revisions.py
import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class VWAPReversion(BaseStrategy):
    """VWAP anchored at session start, with ±2 SD bands."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.std_mult = self.parameters.get("std_mult", 2.0)
        self.rsi_period = self.parameters.get("rsi_period", 14)
        self.rsi_low = self.parameters.get("rsi_low", 30)
        self.rsi_high = self.parameters.get("rsi_high", 70)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(
            [Signal.HOLD] * len(data),
            index=data.index,
            dtype=object,
        )
        if len(data) < self.rsi_period:
            return signals

        typical_price = (data['high'] + data['low'] + data['close']) / 3
        vwap = (typical_price * data['volume']).cumsum() / data['volume'].cumsum()

        rolling_std = (typical_price - vwap).rolling(window=len(data), min_periods=1).std()
        upper_band = vwap + self.std_mult * rolling_std
        lower_band = vwap - self.std_mult * rolling_std

        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        for i in range(self.rsi_period, len(data)):
            if data['close'].iloc[i] <= lower_band.iloc[i] and rsi.iloc[i] < self.rsi_low:
                signals.iloc[i] = Signal.ENTER_LONG
            elif data['close'].iloc[i] >= upper_band.iloc[i] and rsi.iloc[i] > self.rsi_high:
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals