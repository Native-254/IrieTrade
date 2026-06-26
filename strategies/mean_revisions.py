# strategies/mean_reversions.py
import pandas as pd
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from strategies.base import BaseStrategy
from strategies.signals import Signal

class MeanReversion(BaseStrategy):
    """
    Buy when price crosses below lower Bollinger Band and RSI < oversold,
    Sell when price crosses above upper Bollinger Band and RSI > overbought.
    """
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        df = data.copy()
        bb_period = self.parameters.get('bb_period', 20)
        bb_std = self.parameters.get('bb_std', 2.0)
        rsi_period = self.parameters.get('rsi_period', 14)
        rsi_oversold = self.parameters.get('rsi_oversold', 30)
        rsi_overbought = self.parameters.get('rsi_overbought', 70)

        # Bollinger Bands
        indicator_bb = BollingerBands(df['close'], window=bb_period, window_dev=bb_std)
        df['bb_upper'] = indicator_bb.bollinger_hband()
        df['bb_lower'] = indicator_bb.bollinger_lband()

        # RSI
        indicator_rsi = RSIIndicator(df['close'], window=rsi_period)
        df['rsi'] = indicator_rsi.rsi()

        # Initialize signals with object dtype to hold Signal enum values
        signals = pd.Series([Signal.HOLD] * len(df), index=df.index, dtype='object')

        # Buy signal
        buy_cond = (df['close'] < df['bb_lower']) & (df['rsi'] < rsi_oversold)
        signals[buy_cond] = Signal.ENTER_LONG

        # Sell signal (exit long)
        sell_cond = (df['close'] > df['bb_upper']) & (df['rsi'] > rsi_overbought)
        signals[sell_cond] = Signal.EXIT_LONG

        return signals