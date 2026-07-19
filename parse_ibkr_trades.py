# parse_ibkr_trades.py
import pandas as pd
import sys

def parse(filename):
    df = pd.read_csv(filename, parse_dates=['Trade Date'])
    df['PnL'] = 0.0
    # Basic: assume it's a standalone trade list. You'd need to pair buys and sells for actual P&L.
    # This script is a starting point; you can enhance it later.
    print(df.head())
    print(f"Total trades: {len(df)}")
    # further analysis...

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python parse_ibkr_trades.py <path_to_csv>")
    else:
        parse(sys.argv[1])