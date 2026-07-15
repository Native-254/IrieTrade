import pandas as pd
import vectorbt as vbt
from data.manager import DataManager
from strategies.trend_following import TrendFollowingLS
from strategies.mean_revisions import MeanReversion
from strategies.signals import Signal

symbols = [
    'AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META', 'JPM', 'V', 'MA', 'PG', 'DIS', 'HD', 'BAC', 'VZ',
    'ADBE', 'CMCSA', 'NFLX', 'INTC', 'CSCO', 'PFE', 'MRK', 'KO', 'PEP', 'WMT', 'CVX', 'XOM', 'T', 'UNH', 'COST',
    'ORCL', 'ABT', 'CRM', 'NKE', 'MCD', 'IBM', 'LLY', 'MDT', 'BMY', 'AMGN', 'SBUX', 'QCOM', 'TXN', 'GILD', 'FISV',
    'INTU', 'GE', 'BA', 'CAT', 'MMM', 'AXP', 'SPGI', 'DE', 'DUK', 'SO', 'NEE', 'EXC', 'AEP', 'ED', 'D', 'EIX',
    'PEG', 'SRE', 'WEC', 'ES', 'CMS', 'VTI', 'QQQM', 'SMH', 'FDVV', 'FTEC', 'VWO', 'VOO', 'SCHM', 'QQQ',
    'SCHA', 'SCHD', 'VGT'
]

strategy1 = TrendFollowingLS({
    'fast_ma': 50,
    'slow_ma': 200,
    'rsi_period': 14,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
})
strategy2 = MeanReversion({
    'bb_period': 20,
    'bb_std': 2.0,
    'rsi_period': 14,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
})

dm = DataManager()
all_results = []

for symbol in symbols:
    df = dm.get_data(symbol, '2015-01-01', '2025-12-31', interval='1d')
    if df.empty:
        print(f"Skipping {symbol}: no data")
        continue

    sig1 = strategy1.generate_signals(df)
    sig2 = strategy2.generate_signals(df)

    entries = (sig1 == Signal.ENTER_LONG) | (sig2 == Signal.ENTER_LONG)
    exits = (sig1 == Signal.EXIT_LONG) | (sig2 == Signal.EXIT_LONG)

    if entries.sum() == 0 or exits.sum() == 0:
        print(f"Skipping {symbol}: no entry/exit signals")
        continue

    pf = vbt.Portfolio.from_signals(
        close=df['close'],
        entries=entries,
        exits=exits,
        init_cash=10_000,
        freq='D',
        fees=0.001,
        slippage=0.0005,
        size=1
    )

    stats = pf.stats()
    all_results.append(stats)
    print(f"Completed backtest for {symbol}")

if not all_results:
    print("No backtest results available.")
    exit(1)

agg = pd.concat(all_results, axis=1).transpose()
print("\n=== Backtest Summary ===")
print(f"Symbols tested: {len(agg)}")
print(f"Average Total Return: {agg['Total Return [%]'].mean():.2f}%")
print(f"Average Win Rate: {agg['Win Rate [%]'].mean():.2f}%")
print(f"Average Sharpe: {agg['Sharpe Ratio'].mean():.2f}")
print(f"Median Total Return: {agg['Total Return [%]'].median():.2f}%")
print(f"Median Win Rate: {agg['Win Rate [%]'].median():.2f}%")
print(f"Median Sharpe: {agg['Sharpe Ratio'].median():.2f}")
