from __future__ import annotations

import numpy as np
import pandas as pd
import talib

from strategies.base import BaseStrategy
from strategies.signals import Signal


class TALibTrendStrategy(BaseStrategy):
    """Multi-indicator trend strategy using TA-Lib.

    Entry requires:
      - MACD line crosses above signal (long) or below (short)
      - RSI not in overbought (>70) / oversold (<30) territory
      - ADX above threshold confirming trend strength

    Exit when MACD crosses against the position or RSI reverses hard.
    """

    def __init__(self, params: dict | None = None) -> None:
        self.params = params or {}
        super().__init__(self.params)
        self.macd_fast = int(self.params.get("macd_fast", 12))
        self.macd_slow = int(self.params.get("macd_slow", 26))
        self.macd_signal = int(self.params.get("macd_signal", 9))
        self.rsi_period = int(self.params.get("rsi_period", 14))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 20))
        self.rsi_overbought = float(self.params.get("rsi_overbought", 70))
        self.rsi_oversold = float(self.params.get("rsi_oversold", 30))

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        if df.empty or len(df) < max(self.macd_slow, self.adx_period) + 5:
            empty = np.full(len(df), Signal.HOLD, dtype=object)
            return pd.Series(empty, index=df.index)

        # TA-Lib wants clean numpy arrays, not pandas-backed types
        close = df["close"].astype(float).to_numpy(dtype=float)
        high = df["high"].astype(float).to_numpy(dtype=float)
        low = df["low"].astype(float).to_numpy(dtype=float)

        macd, macd_sig, _ = talib.MACD(
            close,
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal,
        )
        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        adx = talib.ADX(high, low, close, timeperiod=self.adx_period)

        macd_series = pd.Series(macd, index=df.index)
        macd_sig_series = pd.Series(macd_sig, index=df.index)
        rsi_series = pd.Series(rsi, index=df.index)
        adx_series = pd.Series(adx, index=df.index)

        cross_above = ((macd_series > macd_sig_series) & (
            macd_series.shift(1) <= macd_sig_series.shift(1)
        )).to_numpy()
        cross_below = ((macd_series < macd_sig_series) & (
            macd_series.shift(1) >= macd_sig_series.shift(1)
        )).to_numpy()

        trend_confirmed = (adx_series > self.adx_threshold).to_numpy()
        not_overbought = (rsi_series < self.rsi_overbought).to_numpy()
        not_oversold = (rsi_series > self.rsi_oversold).to_numpy()

        # Build the signal array directly — numpy is permissive with object dtype,
        # pandas' stubs are not, so we wrap in a Series only at the end.
        signals: np.ndarray = np.full(len(df), Signal.HOLD, dtype=object)

        signals[cross_above & trend_confirmed & not_overbought] = Signal.ENTER_LONG
        signals[cross_below & trend_confirmed & not_oversold] = Signal.ENTER_SHORT

        # Exits applied last so they override entries when both fire on the same bar
        signals[cross_below] = Signal.EXIT_LONG
        signals[cross_above] = Signal.EXIT_SHORT

        return pd.Series(signals, index=df.index)