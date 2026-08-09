import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class ADXTrendFilter(BaseStrategy):
    """Trend-following using ADX + DI crossovers."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.period = self.parameters.get("period", 14)
        self.adx_threshold = self.parameters.get("adx_threshold", 20)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data["high"]
        low = data["low"]
        close = data["close"]

        tr = pd.DataFrame(
            {
                "tr1": high - low,
                "tr2": (high - close.shift()).abs(),
                "tr3": (low - close.shift()).abs(),
            }
        ).max(axis=1)
        atr = tr.rolling(self.period).mean()

        up_move = high.diff()
        down_move = low.shift() - low

        plus_dm = pd.Series(0.0, index=data.index)
        minus_dm = pd.Series(0.0, index=data.index)
        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        plus_di = 100 * (plus_dm.rolling(self.period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(self.period).mean() / atr)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.rolling(self.period).mean()

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        for i in range(1, len(data)):
            if adx.iloc[i] > self.adx_threshold:
                if (
                    plus_di.iloc[i] > minus_di.iloc[i]
                    and plus_di.iloc[i - 1] <= minus_di.iloc[i - 1]
                ):
                    signals.iloc[i] = Signal.ENTER_LONG
                elif (
                    minus_di.iloc[i] > plus_di.iloc[i]
                    and minus_di.iloc[i - 1] <= plus_di.iloc[i - 1]
                ):
                    signals.iloc[i] = Signal.ENTER_SHORT

        return signals
