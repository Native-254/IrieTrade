# backtest/backtest_multi.py
"""Run the backtest engine over all symbols in the configuration."""
from backtest.engine import BacktestEngine
from data.manager import DataManager
from utils.config import CONFIG
from utils.logger import log


def main():
    symbols = CONFIG["trading"]["symbols"]
    start_date = "2020-01-01"
    end_date = "2024-01-01"

    data_mgr = DataManager()
    engine = BacktestEngine()

    # Fetch all historical data into a dict keyed by symbol
    data = {}
    for symbol in symbols:
        log.info(f"Fetching data for {symbol} …")
        df = data_mgr.get_data(symbol, start_date=start_date, end_date=end_date,
                               interval="1d", force_refresh=False)
        if df.empty:
            log.warning(f"No data for {symbol}, skipping.")
            continue
        data[symbol] = df

    if not data:
        log.error("No data for any symbol. Exiting.")
        return

    result = engine.run(symbols=list(data.keys()), data=data,
                        start_date=start_date, end_date=end_date)
    if "error" in result:
        log.error(f"Backtest failed: {result['error']}")
        return

    print("\nBacktest Summary")
    print("=" * 60)
    print(f"Final capital:   ${result['final_capital']:,.2f}")
    print(f"Total return:    {result['total_return'] * 100:+.2f}%")
    print(f"Sharpe ratio:    {result['sharpe_ratio']:.3f}")
    print(f"Max drawdown:    {result['max_drawdown'] * 100:.2f}%")
    print(f"Win rate:        {result['win_rate'] * 100:.1f}%")
    print(f"Total trades:    {result['total_trades']}")
    print("=" * 60)


if __name__ == "__main__":
    main()