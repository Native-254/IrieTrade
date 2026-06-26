# backtest/engine.py
import vectorbt as vbt
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
from utils.logger import log
from data.manager import DataManager
from strategies.trend_following import TrendFollowingLS
from strategies.mean_revisions import MeanReversion


def _to_stats_dict(stats: Any) -> Dict[str, Any]:
    if stats is None:
        return {}
    if isinstance(stats, pd.Series):
        return {str(k): v for k, v in stats.to_dict().items()}
    try:
        return {str(k): v for k, v in dict(stats).items()}
    except Exception:
        return {}


@dataclass
class Position:
    """Simulated trading position."""
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: int
    entry_price: float
    entry_date: pd.Timestamp

class PositionManager:
    """Manages open positions during backtest."""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
    
    def open_position(self, symbol: str, side: str, quantity: int, entry_price: float, entry_date: pd.Timestamp):
        """Open a new position."""
        if symbol in self.positions:
            self.close_position(symbol, entry_price, entry_date)
        self.positions[symbol] = Position(symbol, side, quantity, entry_price, entry_date)
    
    def close_position(self, symbol: str, exit_price: float, exit_date: pd.Timestamp) -> Tuple[float, float]:
        """Close a position and return (pnl, pnl_percent)."""
        if symbol not in self.positions:
            return 0.0, 0.0
        pos = self.positions[symbol]
        if pos.side == 'BUY':
            pnl = (exit_price - pos.entry_price) * pos.quantity
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:  # SELL
            pnl = (pos.entry_price - exit_price) * pos.quantity
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price
        del self.positions[symbol]
        return pnl, pnl_pct
    
    def get_open_count(self) -> int:
        return len(self.positions)

class BacktestEngine:
    def __init__(self, initial_capital: float = 100000.0, commission: float = 0.001):
        self.initial_capital = initial_capital
        self.commission = commission
        self.data_manager = DataManager()

    def run_backtest(self, strategy_name: str, symbol: str, start_date: str, end_date: str, strategy_params: dict) -> Dict[str, Any]:
        """Runs a backtest for a single strategy and returns metrics."""
        log.info(f"Running backtest for {strategy_name} on {symbol} from {start_date} to {end_date}")

        # 1. Fetch Data
        data = self.data_manager.get_data(symbol, start_date, end_date, interval="1d")
        if data.empty:
            log.error("Backtest aborted: No data available.")
            return {}

        # 2. Instantiate Strategy and Generate Signals
        if strategy_name == 'TrendFollowing':
            strategy = TrendFollowingLS(strategy_params)
        elif strategy_name == 'MeanReversion':
            strategy = MeanReversion(strategy_params)
        else:
            log.error(f"Strategy '{strategy_name}' not found.")
            return {}

        signals_series = strategy.generate_signals(data)

        # Convert signals to BUY/SELL format
        entries = signals_series.isin(['BUY', 'ENTER_LONG'])
        exits = signals_series.isin(['SELL', 'EXIT_LONG'])

        # 3. Run VectorBT Portfolio Simulation
        price = data['close']
        pf = vbt.Portfolio.from_signals(
            price,
            entries=entries,
            exits=exits,
            init_cash=self.initial_capital,
            fees=self.commission,
            freq='D'
        )

        # 4. Extract Key Metrics
        stats = pf.stats()
        if stats is None:
            log.error('Backtest aborted: unable to compute portfolio statistics.')
            return {}

        stats_map = _to_stats_dict(stats)
        metrics = {
            'strategy': strategy_name,
            'symbol': symbol,
            'start_value': float(stats_map.get('Start Value') or 0.0),
            'end_value': float(stats_map.get('End Value') or 0.0),
            'total_return': float(stats_map.get('Total Return [%]') or 0.0),
            'max_drawdown': float(stats_map.get('Max Drawdown [%]') or 0.0),
            'sharpe_ratio': float(stats_map.get('Sharpe Ratio') or 0.0),
            'win_rate': float(stats_map.get('Win Rate [%]') or 0.0),
            'total_trades': int(stats_map.get('Total Trades') or 0)
        }
        log.success(f"Backtest complete. {strategy_name} on {symbol}: Return={metrics['total_return']:.2f}%, Sharpe={metrics['sharpe_ratio']:.2f}")
        return metrics

    def run_multi_strategy_backtest(self, strategies: List[Dict[str, Any]], symbol: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Runs backtest for multiple strategies on the same symbol.
        Combines signals with OR logic (any strategy's signal wins).
        
        Args:
            strategies: List of dicts with keys: 'name', 'params'
            symbol: Stock symbol
            start_date, end_date: Date range
        
        Returns:
            Combined backtest metrics
        """
        log.info(f"Running multi-strategy backtest on {symbol} with {len(strategies)} strategies")

        # 1. Fetch Data
        data = self.data_manager.get_data(symbol, start_date, end_date, interval="1d")
        if data.empty:
            log.error("Backtest aborted: No data available.")
            return {}

        # 2. Generate signals from all strategies
        combined_entries = pd.Series(False, index=data.index)
        combined_exits = pd.Series(False, index=data.index)
        
        for strat_config in strategies:
            name = strat_config['name']
            params = strat_config['params']
            
            if name == 'TrendFollowing':
                strategy = TrendFollowingLS(params)
            elif name == 'MeanReversion':
                strategy = MeanReversion(params)
            else:
                log.warning(f"Strategy '{name}' not found. Skipping.")
                continue
            
            signals = strategy.generate_signals(data)
            entries = signals.isin(['BUY', 'ENTER_LONG'])
            exits = signals.isin(['SELL', 'EXIT_LONG'])
            
            # Combine with OR logic
            combined_entries = combined_entries | entries
            combined_exits = combined_exits | exits
            log.debug(f"{name}: {entries.sum()} buy signals, {exits.sum()} sell signals")

        # 3. Run VectorBT Portfolio Simulation
        price = data['close']
        pf = vbt.Portfolio.from_signals(
            price,
            entries=combined_entries,
            exits=combined_exits,
            init_cash=self.initial_capital,
            fees=self.commission,
            freq='D'
        )

        # 4. Extract Key Metrics
        stats = pf.stats()
        if stats is None:
            log.error('Backtest aborted: unable to compute portfolio statistics.')
            return {}

        stats_map = _to_stats_dict(stats)
        metrics = {
            'strategies': [s['name'] for s in strategies],
            'symbol': symbol,
            'start_value': float(stats_map.get('Start Value') or 0.0),
            'end_value': float(stats_map.get('End Value') or 0.0),
            'total_return': float(stats_map.get('Total Return [%]') or 0.0),
            'max_drawdown': float(stats_map.get('Max Drawdown [%]') or 0.0),
            'sharpe_ratio': float(stats_map.get('Sharpe Ratio') or 0.0),
            'win_rate': float(stats_map.get('Win Rate [%]') or 0.0),
            'total_trades': int(stats_map.get('Total Trades') or 0)
        }
        
        strat_names = ', '.join([s['name'] for s in strategies])
        log.success(f"Multi-strategy backtest complete ({strat_names}): Return={metrics['total_return']:.2f}%, Sharpe={metrics['sharpe_ratio']:.2f}")
        return metrics

    def run_multi_symbol_backtest(self, strategy_name: str, symbols: List[str], start_date: str, end_date: str, strategy_params: dict) -> Dict[str, Any]:
        """
        Runs backtest for a single strategy across multiple symbols.
        Returns aggregated portfolio metrics.
        
        Args:
            strategy_name: Name of strategy
            symbols: List of stock symbols
            start_date, end_date: Date range
            strategy_params: Strategy parameters
        
        Returns:
            Combined portfolio metrics
        """
        log.info(f"Running {strategy_name} backtest on {len(symbols)} symbols")
        
        all_results = []
        for symbol in symbols:
            result = self.run_backtest(strategy_name, symbol, start_date, end_date, strategy_params)
            if result:
                all_results.append(result)
        
        if not all_results:
            log.error("No successful backtests")
            return {}
        
        # Aggregate metrics
        avg_return = np.mean([r['total_return'] for r in all_results])
        avg_sharpe = np.mean([r['sharpe_ratio'] for r in all_results])
        avg_drawdown = np.mean([r['max_drawdown'] for r in all_results])
        avg_win_rate = np.mean([r['win_rate'] for r in all_results])
        total_trades = sum([r['total_trades'] for r in all_results])
        
        metrics = {
            'strategy': strategy_name,
            'num_symbols': len(symbols),
            'avg_total_return': avg_return,
            'avg_max_drawdown': avg_drawdown,
            'avg_sharpe_ratio': avg_sharpe,
            'avg_win_rate': avg_win_rate,
            'total_trades_all': total_trades,
            'symbol_results': all_results
        }
        
        log.success(f"Multi-symbol backtest complete: Avg Return={avg_return:.2f}%, Avg Sharpe={avg_sharpe:.2f}")
        return metrics

if __name__ == '__main__':
    # Example: Single strategy
    engine = BacktestEngine()
    result = engine.run_backtest(
        strategy_name='TrendFollowing',
        symbol='AAPL',
        start_date='2020-01-01',
        end_date='2023-12-31',
        strategy_params={'fast_ma': 20, 'slow_ma': 50}
    )
    print("Single Strategy Result:", result)
    
    # Example: Multi-strategy
    multi_result = engine.run_multi_strategy_backtest(
        strategies=[
            {'name': 'TrendFollowing', 'params': {'fast_ma': 20, 'slow_ma': 50}},
            {'name': 'MeanReversion', 'params': {'bb_period': 20, 'bb_std': 2.0}}
        ],
        symbol='AAPL',
        start_date='2020-01-01',
        end_date='2023-12-31'
    )
    print("Multi-Strategy Result:", multi_result)
