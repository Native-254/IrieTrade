import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class AroonCross(BaseStrategy):
    """Aroon up/down crossover strategy."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.period = self.parameters.get("period", 25)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data["high"]
        low = data["low"]

        aroon_up = high.rolling(self.period).apply(
            lambda x: 100 * (x.argmax() + 1) / self.period,
            raw=True,
        )
        aroon_down = low.rolling(self.period).apply(
            lambda x: 100 * (x.argmin() + 1) / self.period,
            raw=True,
        )

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        for i in range(1, len(data)):
            if (
                aroon_up.iloc[i] > aroon_down.iloc[i]
                and aroon_up.iloc[i - 1] <= aroon_down.iloc[i - 1]
            ):
                signals.iloc[i] = Signal.ENTER_LONG
            elif (
                aroon_down.iloc[i] > aroon_up.iloc[i]
                and aroon_down.iloc[i - 1] <= aroon_up.iloc[i - 1]
            ):
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals
