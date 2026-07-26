# backtest/backtest_multi.py
"""Run the backtest engine over all symbols in the configuration."""

from backtest.engine import BacktestEngine
from data.manager import DataManager
from utils.config import CONFIG
from utils.logger import log


def main():
    symbols = CONFIG["trading"]["symbols"]
    start_date = "2015-01-01"
    end_date = "2025-12-31"

    data_mgr = DataManager()
    engine = BacktestEngine()

    results = []
    for symbol in symbols:
        log.info(f"Backtesting {symbol} …")
        df = data_mgr.get_data(
            symbol,
            start_date=start_date,
            end_date=end_date,
            interval="1d",
            force_refresh=False,
        )
        if df.empty:
            log.warning(f"No data for {symbol}, skipping.")
            continue

        result = engine.run(symbol, df, start_date=start_date, end_date=end_date)
        if "error" in result:
            log.error(f"Backtest failed for {symbol}: {result['error']}")
            continue

        final_return = (
            (result["final_capital"] - engine.initial_capital)
            / engine.initial_capital
            * 100
        )
        trades = result["total_trades"]
        log.success(f"{symbol}: {trades} trades, final return {final_return:+.2f}%")
        results.append((symbol, trades, final_return))

    # Summary
    print("\nBacktest Summary")
    print("=" * 50)
    for sym, tr, ret in sorted(results, key=lambda x: x[2], reverse=True):
        print(f"{sym:<8}  {tr:4d} trades  {ret:+8.2f}%")
    print("=" * 50)


if __name__ == "__main__":
    main()
