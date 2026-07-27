# strategies/orb.py
import pandas as pd

from strategies.base import BaseStrategy
from strategies.signals import Signal


class OpeningRangeBreakout(BaseStrategy):
    """ORB strategy using 30‑min opening range on 15‑minute bars."""

    def __init__(self, parameters: dict | None = None):
        super().__init__(parameters or {})
        self.orb_period = 2  # 2 x 15m bars = 30 min
        self.volume_ratio_threshold = self.parameters.get("volume_ratio_threshold", 1.5)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(
            [Signal.HOLD] * len(data),
            index=data.index,
            dtype=object,
        )
        if len(data) < self.orb_period:
            return signals

        if data.index[-1].date() != data.index[0].date():
            return signals

        orb_high = data['high'].iloc[: self.orb_period].max()
        orb_low = data['low'].iloc[: self.orb_period].min()

        avg_vol_orb = data['volume'].iloc[: self.orb_period].mean()

        for i in range(self.orb_period, len(data)):
            price = data['close'].iloc[i]
            vol = data['volume'].iloc[i]
            rel_vol = vol / avg_vol_orb if avg_vol_orb > 0 else 1.0

            if price > orb_high and rel_vol > self.volume_ratio_threshold:
                signals.iloc[i] = Signal.ENTER_LONG
            elif price < orb_low and rel_vol > self.volume_ratio_threshold:
                signals.iloc[i] = Signal.ENTER_SHORT

        return signals