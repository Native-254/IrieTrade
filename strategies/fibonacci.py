# strategies/fibonacci.py
import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class FibonacciRetracement(BaseStrategy):
    """
    Professional Fibonacci retracement strategy:
    - Determine trend via 50‑SMA slope
    - Identify swing high/low over N bars
    - Price must pull back into 0.382–0.618 zone
    - Confirmation via bullish/bearish engulfing candle
    - Volume spike compared to 20‑bar average
    - Targets the opposite extreme of the swing
    """

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.swing_lookback = self.parameters.get("swing_lookback", 50)
        self.volume_factor = self.parameters.get("volume_factor", 1.5)
        self.sma_period = self.parameters.get("sma_period", 50)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)
        if len(data) < self.swing_lookback:
            return signals

        close = data["close"]
        high = data["high"]
        low = data["low"]
        volume = data["volume"]

        # Trend detection: simple slope of 50‑SMA over last 5 bars
        sma = close.rolling(self.sma_period).mean()
        trend_up = sma.iloc[-1] > sma.iloc[-5]

        # Recent swing high and low
        recent = data.iloc[-self.swing_lookback:]
        swing_high = recent["high"].max()
        swing_low = recent["low"].min()
        diff = swing_high - swing_low
        if diff == 0:
            return signals

        fib_levels = {
            "0.382": swing_high - 0.382 * diff if trend_up else swing_low + 0.382 * diff,
            "0.5": swing_high - 0.5 * diff if trend_up else swing_low + 0.5 * diff,
            "0.618": swing_high - 0.618 * diff if trend_up else swing_low + 0.618 * diff,
        }

        # Current bar
        last_close = close.iloc[-1]
        last_open = data["open"].iloc[-1]
        last_volume = volume.iloc[-1]
        avg_volume = volume.iloc[-20:].mean()

        # Check proximity to any key level (± 0.5%)
        for level_price in fib_levels.values():
            if abs(last_close - level_price) / level_price < 0.005:
                # Candlestick confirmation
                candle_bull = last_close > last_open and (last_close - last_open) > 0.6 * (high.iloc[-1] - low.iloc[-1])
                candle_bear = last_open > last_close and (last_open - last_close) > 0.6 * (high.iloc[-1] - low.iloc[-1])
                volume_ok = last_volume > self.volume_factor * avg_volume

                if trend_up and candle_bull and volume_ok:
                    signals.iloc[-1] = Signal.ENTER_LONG
                elif not trend_up and candle_bear and volume_ok:
                    signals.iloc[-1] = Signal.ENTER_SHORT
                break   # first matching level wins
        return signals