# backtest_multi.py
from backtest.engine import BacktestEngine
from strategies.trend_following import TrendFollowingLS
from strategies.mean_revisions import MeanReversion
from strategies.trend_following_long_only import TrendFollowingLongOnly
from utils.config import CONFIG

config = CONFIG

strategies = [
    TrendFollowingLS(config['strategies']['parameters']['trend_following_ls']),
    MeanReversion(config['strategies']['parameters']['mean_reversion']),
    TrendFollowingLongOnly(config['strategies']['parameters'].get(
        'trend_following_long_only',
        {'fast_ma': 50, 'slow_ma': 200, 'rsi_period': 14, 'rsi_oversold': 30, 'rsi_overbought': 70}
    ))
]

engine = BacktestEngine(config, strategies)

symbols = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META', 'JPM', 'V', 'MA', 'PG', 'DIS', 'HD', 'BAC', 'VZ',
           'ADBE', 'CMCSA', 'NFLX', 'INTC', 'CSCO', 'PFE', 'MRK', 'KO', 'PEP', 'WMT', 'CVX', 'XOM', 'T', 'UNH', 'COST',
           'ORCL', 'ABT', 'CRM', 'NKE', 'MCD', 'IBM', 'LLY', 'MDT', 'BMY', 'AMGN', 'SBUX', 'QCOM', 'TXN', 'GILD', 'FISV',
           'INTU', 'GE', 'BA', 'CAT', 'MMM', 'AXP', 'SPGI', 'DE', 'DUK', 'SO', 'NEE', 'EXC', 'AEP', 'ED', 'D', 'EIX', 'PEG',
           'SRE', 'WEC', 'ES', 'CMS', 'VTI', 'QQQM', 'SMH', 'FDVV', 'FTEC', 'VWO', 'VOO', 'SCHM', 'QQQ', 'SCHA', 'SCHD', 'VGT']

results = engine.run(symbols, '2015-01-01', '2025-12-31')
for sym, stats in results.items():
    print(f"{sym}: {stats['total_trades']} trades, Win Rate {stats['win_rate']:.2%}, Total PnL ${stats['total_pnl']:,.2f}")