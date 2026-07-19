# strategies/trend_following_long_only.py
import pandas as pd
from ta.trend import SMAIndicator
from ta.momentum import RSIIndicator
from strategies.base import BaseStrategy
from strategies.signals import Signal

class TrendFollowingLongOnly(BaseStrategy):
    """
    Long when fast MA > slow MA and RSI < oversold.
    Exit long when fast MA < slow MA or RSI > overbought.
    """
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        df = data.copy()
        fast_ma = self.parameters.get('fast_ma', 50)
        slow_ma = self.parameters.get('slow_ma', 200)
        rsi_period = self.parameters.get('rsi_period', 14)
        rsi_oversold = self.parameters.get('rsi_oversold', 30)
        rsi_overbought = self.parameters.get('rsi_overbought', 70)

        df['ma_fast'] = SMAIndicator(df['close'], window=fast_ma).sma_indicator()
        df['ma_slow'] = SMAIndicator(df['close'], window=slow_ma).sma_indicator()
        df['rsi'] = RSIIndicator(df['close'], window=rsi_period).rsi()

        signals = pd.Series(index=df.index, dtype='object')
        signals[:] = Signal.HOLD

        buy_cond = (df['ma_fast'] > df['ma_slow']) & (df['rsi'] < rsi_oversold)
        signals[buy_cond] = Signal.ENTER_LONG

        sell_cond = (df['ma_fast'] < df['ma_slow']) | (df['rsi'] > rsi_overbought)
        signals[sell_cond] = Signal.EXIT_LONG

        return signals