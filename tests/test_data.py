# test_data.py
from data.manager import DataManager

dm = DataManager()
df = dm.get_data('AAPL', '2025-01-01', '2026-01-01', interval='1d')
print(df.head())
print(f"\nTotal rows: {len(df)}")
