# test_strategy.py
from strategies.trend_following import TrendFollowing
from data.manager import DataManager

dm = DataManager()
df = dm.get_data('AAPL', '2024-01-01', '2026-01-01')
strat = TrendFollowing({'fast_ma': 20, 'slow_ma': 50})
signals = strat.generate_signals(df)
print("Last 10 signals:")
print(signals.tail(10))

