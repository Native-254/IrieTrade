import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class StochasticCross(BaseStrategy):
    """Stochastic oscillator %K / %D crossover."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.k_period = self.parameters.get("k_period", 14)
        self.d_period = self.parameters.get("d_period", 3)
        self.overbought = self.parameters.get("overbought", 80)
        self.oversold = self.parameters.get("oversold", 20)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        low_min = data["low"].rolling(self.k_period).min()
        high_max = data["high"].rolling(self.k_period).max()
        range_ = high_max - low_min
        fast_k = 100 * (data["close"] - low_min) / range_
        fast_k = fast_k.where(range_ != 0, 0).fillna(0)
        slow_d = fast_k.rolling(self.d_period).mean()

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        for i in range(1, len(data)):
            if (
                fast_k.iloc[i] > slow_d.iloc[i]
                and fast_k.iloc[i - 1] <= slow_d.iloc[i - 1]
                and fast_k.iloc[i] < self.oversold
            ):
                signals.iloc[i] = Signal.ENTER_LONG
            elif (
                fast_k.iloc[i] < slow_d.iloc[i]
                and fast_k.iloc[i - 1] >= slow_d.iloc[i - 1]
                and fast_k.iloc[i] > self.overbought
            ):
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals
