import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class MACDCross(BaseStrategy):
    """MACD line / signal line crossover strategy."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.fast = self.parameters.get("fast", 12)
        self.slow = self.parameters.get("slow", 26)
        self.signal_period = self.parameters.get("signal", 9)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        for i in range(1, len(data)):
            if (
                macd_line.iloc[i] > signal_line.iloc[i]
                and macd_line.iloc[i - 1] <= signal_line.iloc[i - 1]
            ):
                signals.iloc[i] = Signal.ENTER_LONG
            elif (
                macd_line.iloc[i] < signal_line.iloc[i]
                and macd_line.iloc[i - 1] >= signal_line.iloc[i - 1]
            ):
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals
