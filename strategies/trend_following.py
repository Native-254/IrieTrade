# strategies/trend_following_ls.py
import pandas as pd
from ta.trend import sma_indicator
from ta.momentum import rsi as ta_rsi
from strategies.base import BaseStrategy
from strategies.signals import Signal

class TrendFollowingLS(BaseStrategy):
    """
    Long: fast MA > slow MA and RSI < oversold
    Short: fast MA < slow MA and RSI > overbought
    """
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        df = data.copy()
        fast_ma = self.parameters.get('fast_ma', 50)
        slow_ma = self.parameters.get('slow_ma', 200)
        rsi_period = self.parameters.get('rsi_period', 14)
        rsi_oversold = self.parameters.get('rsi_oversold', 30)
        rsi_overbought = self.parameters.get('rsi_overbought', 70)

        df['ma_fast'] = sma_indicator(df['close'], window=fast_ma)
        df['ma_slow'] = sma_indicator(df['close'], window=slow_ma)
        df['rsi'] = ta_rsi(df['close'], window=rsi_period)

        signals = pd.Series([Signal.HOLD] * len(df), index=df.index, dtype='object')

        long_cond = (df['ma_fast'] > df['ma_slow']) & (df['rsi'] < rsi_oversold)
        signals[long_cond] = Signal.ENTER_LONG

        short_cond = (df['ma_fast'] < df['ma_slow']) & (df['rsi'] > rsi_overbought)
        signals[short_cond] = Signal.ENTER_SHORT

        return signals