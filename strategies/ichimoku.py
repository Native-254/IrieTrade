# strategies/ichimoku.py
import numpy as np  # noqa: F401
import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class IchimokuCloud(BaseStrategy):
    """Ichimoku Cloud strategy with multiple confirmation signals."""
    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.tenkan_period = self.parameters.get("tenkan", 9)
        self.kijun_period = self.parameters.get("kijun", 26)
        self.senkou_b_period = self.parameters.get("senkou_b", 52)
        self.chikou_shift = self.parameters.get("chikou_shift", 26)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data["high"]
        low = data["low"]
        close = data["close"]

        # Tenkan‑sen (Conversion Line)
        tenkan = (high.rolling(self.tenkan_period).max() + low.rolling(self.tenkan_period).min()) / 2
        # Kijun‑sen (Base Line)
        kijun = (high.rolling(self.kijun_period).max() + low.rolling(self.kijun_period).min()) / 2
        # Senkou Span A (Leading Span A)
        senkou_a = ((tenkan + kijun) / 2).shift(self.kijun_period)
        # Senkou Span B (Leading Span B)
        senkou_b = ((high.rolling(self.senkou_b_period).max() + low.rolling(self.senkou_b_period).min()) / 2).shift(self.kijun_period)
        # Chikou Span (Lagging Span) – price shifted backwards
        chikou = close.shift(-self.chikou_shift)

        signals = pd.Series([Signal.HOLD] * len(data), index=data.index, dtype=object)

        for i in range(self.senkou_b_period + self.kijun_period, len(data)):
            # All components must be valid
            if pd.isna(tenkan.iloc[i]) or pd.isna(kijun.iloc[i]) or pd.isna(senkou_a.iloc[i]) or pd.isna(senkou_b.iloc[i]) or pd.isna(chikou.iloc[i - self.chikou_shift]):
                continue

            tk_cross_bull = tenkan.iloc[i] > kijun.iloc[i] and tenkan.iloc[i - 1] <= kijun.iloc[i - 1]
            tk_cross_bear = tenkan.iloc[i] < kijun.iloc[i] and tenkan.iloc[i - 1] >= kijun.iloc[i - 1]

            # Cloud condition: price must be above both Senkou lines for long, below both for short
            above_cloud = close.iloc[i] > max(senkou_a.iloc[i], senkou_b.iloc[i])
            below_cloud = close.iloc[i] < min(senkou_a.iloc[i], senkou_b.iloc[i])

            # Chikou Span confirmation: must be above price for long, below for short (shifted back)
            chikou_above = chikou.iloc[i - self.chikou_shift] > close.iloc[i - self.chikou_shift]
            chikou_below = chikou.iloc[i - self.chikou_shift] < close.iloc[i - self.chikou_shift]

            # Future cloud bullish: Senkou A > Senkou B
            future_bull = senkou_a.iloc[i] > senkou_b.iloc[i]
            future_bear = senkou_a.iloc[i] < senkou_b.iloc[i]

            if tk_cross_bull and above_cloud and chikou_above and future_bull:
                signals.iloc[i] = Signal.ENTER_LONG
            elif tk_cross_bear and below_cloud and chikou_below and future_bear:
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals