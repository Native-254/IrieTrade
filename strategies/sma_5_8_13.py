from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class SMA5813Strategy(BaseStrategy):
    """5-8-13 simple moving average crossover strategy."""

    def __init__(self, parameters: dict | None = None) -> None:
        super().__init__(parameters or {})
        self.fast = int(self.parameters.get("fast_ma", 5))
        self.mid = int(self.parameters.get("mid_ma", 8))
        self.slow = int(self.parameters.get("slow_ma", 13))

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Return a Series of Signal values indexed like data."""
        close = data["close"]

        sma_fast = close.rolling(self.fast).mean()
        sma_mid = close.rolling(self.mid).mean()
        sma_slow = close.rolling(self.slow).mean()

        fast_cross_above_mid = (sma_fast > sma_mid) & (
            sma_fast.shift(1) <= sma_mid.shift(1)
        )
        fast_cross_below_mid = (sma_fast < sma_mid) & (
            sma_fast.shift(1) >= sma_mid.shift(1)
        )

        bullish_alignment = (sma_fast > sma_mid) & (sma_mid > sma_slow)
        bearish_alignment = (sma_fast < sma_mid) & (sma_mid < sma_slow)

        signals = pd.Series(Signal.HOLD, index=data.index, dtype=object)

        signals[fast_cross_below_mid] = Signal.EXIT_LONG
        signals[fast_cross_above_mid] = Signal.EXIT_SHORT
        signals[(close < sma_slow) & (sma_fast < sma_mid)] = Signal.EXIT_LONG
        signals[(close > sma_slow) & (sma_fast > sma_mid)] = Signal.EXIT_SHORT

        long_entry = (
            fast_cross_above_mid
            & bullish_alignment
            & (close > sma_fast)
        )
        short_entry = (
            fast_cross_below_mid
            & bearish_alignment
            & (close < sma_fast)
        )

        signals[long_entry] = Signal.ENTER_LONG
        signals[short_entry] = Signal.ENTER_SHORT

        return signals.fillna(Signal.HOLD)
