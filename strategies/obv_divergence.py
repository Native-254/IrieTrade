import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class OBVDivergence(BaseStrategy):
    """OBV divergence from price – signals potential reversals."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.lookback = self.parameters.get("lookback", 20)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        volume = data["volume"]
        direction = close.diff().apply(
            lambda x: 1 if x > 0 else -1 if x < 0 else 0
        )
        obv = (volume * direction).cumsum()

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        for i in range(self.lookback, len(data)):
            price_window = close.iloc[i - self.lookback : i + 1]
            obv_window = obv.iloc[i - self.lookback : i + 1]
            previous_price_low = price_window.iloc[:-1].min()
            previous_price_high = price_window.iloc[:-1].max()
            previous_obv_low = obv_window.iloc[:-1].min()
            previous_obv_high = obv_window.iloc[:-1].max()

            if (
                price_window.iloc[-1] < previous_price_low
                and obv_window.iloc[-1] > previous_obv_low
            ):
                signals.iloc[i] = Signal.ENTER_LONG
            elif (
                price_window.iloc[-1] > previous_price_high
                and obv_window.iloc[-1] < previous_obv_high
            ):
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals
