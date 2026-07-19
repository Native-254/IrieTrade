# backtest/engine.py
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from data.manager import DataManager
from strategies.signals import Signal

@dataclass
class BacktestPosition:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: Optional[float] = None

class BacktestEngine:
    def __init__(self, config: dict, strategies: List):
        self.config = config
        self.strategies = strategies
        exec_cfg = config['execution']
        self.slippage = exec_cfg.get('slippage_percent', 0.0005)
        self.comm_per_share = exec_cfg['commission_per_share']
        self.comm_min = exec_cfg['commission_min']
        self.comm_max_pct = exec_cfg['commission_max_pct']
        self.partial_fill = exec_cfg.get('simulate_partial_fills', False)
        self.partial_min = exec_cfg.get('partial_fill_min_ratio', 0.8)
        risk_cfg = config['risk_management']
        self.vol_stop_mult = risk_cfg['volatility_stop_multiplier']
        self.risk_per_trade = risk_cfg['max_capital_per_trade']

    def _apply_slippage(self, price: float, action: str) -> float:
        if action in ('BUY', 'BUY_TO_COVER'):
            return price * (1 + self.slippage)
        else:
            return price * (1 - self.slippage)

    def _commission(self, qty: int, price: float) -> float:
        trade_value = qty * price
        per_share = qty * self.comm_per_share
        return max(self.comm_min, min(per_share, trade_value * self.comm_max_pct))

    def _simulate_fill(self, qty: int) -> int:
        if not self.partial_fill:
            return qty
        ratio = np.random.uniform(self.partial_min, 1.0)
        return max(1, int(qty * ratio))

    def backtest_symbol(self, symbol: str, start_date: str, end_date: str,
                        initial_capital: float = 100000.0) -> pd.DataFrame:
        dm = DataManager()
        df = dm.get_data(symbol, start_date, end_date, interval='1d')
        if df.empty:
            return pd.DataFrame()

        # Generate signals for all strategies
        signals = {}
        for s in self.strategies:
            sig = s.generate_signals(df)
            signals[s.__class__.__name__] = sig

        position = None
        trades = []
        capital = initial_capital

        for i in range(len(df)):
            date = df.index[i]
            price = df['close'].iloc[i]

            enter_long = any(sig.iloc[i] == Signal.ENTER_LONG for sig in signals.values())
            exit_long  = any(sig.iloc[i] == Signal.EXIT_LONG  for sig in signals.values())
            enter_short= any(sig.iloc[i] == Signal.ENTER_SHORT for sig in signals.values())
            exit_short = any(sig.iloc[i] == Signal.EXIT_SHORT for sig in signals.values())

            current_side = position.side if position else None
            action = None

            if exit_long and current_side == 'BUY':
                action = 'SELL'
            elif exit_short and current_side == 'SELL':
                action = 'BUY_TO_COVER'
            elif enter_long and current_side is None:
                action = 'BUY'
            elif enter_long and current_side == 'SELL':
                action = 'BUY_TO_COVER'
            elif enter_short and current_side is None:
                action = 'SELL_SHORT'
            elif enter_short and current_side == 'BUY':
                action = 'SELL'

            atr = (df['high'] - df['low']).rolling(14).mean().iloc[i]

            if action and position is None:   # new entry
                if action == 'BUY':
                    stop_loss = price - (atr * self.vol_stop_mult)
                    tp = price + (atr * self.vol_stop_mult * 2)
                    risk_amount = capital * self.risk_per_trade
                    qty = int(risk_amount / (atr * self.vol_stop_mult)) if atr > 0 else 10
                    qty = max(1, self._simulate_fill(qty))
                    entry = self._apply_slippage(price, 'BUY')
                    comm = self._commission(qty, entry)
                    position = BacktestPosition(symbol, 'BUY', qty, entry + comm/qty, stop_loss, tp)
                elif action == 'SELL_SHORT':
                    stop_loss = price + (atr * self.vol_stop_mult)
                    tp = price - (atr * self.vol_stop_mult * 2)
                    risk_amount = capital * self.risk_per_trade
                    qty = int(risk_amount / (atr * self.vol_stop_mult)) if atr > 0 else 10
                    qty = max(1, self._simulate_fill(qty))
                    entry = self._apply_slippage(price, 'SELL_SHORT')
                    comm = self._commission(qty, entry)
                    position = BacktestPosition(symbol, 'SELL', qty, entry - comm/qty, stop_loss, tp)

            elif action and position:   # close existing position
                exit_price = self._apply_slippage(price, action)
                comm = self._commission(position.quantity, exit_price)
                net_exit = exit_price + (comm / position.quantity) if action == 'BUY_TO_COVER' else exit_price - (comm / position.quantity)
                if position.side == 'BUY':
                    pnl = (net_exit - position.entry_price) * position.quantity
                else:
                    pnl = (position.entry_price - net_exit) * position.quantity
                capital += pnl
                trades.append({'date': date, 'symbol': symbol, 'action': action, 'pnl': pnl})
                position = None

            # Stop‑loss / take‑profit check
            if position:
                hit = False
                if position.side == 'BUY':
                    if price <= position.stop_loss:
                        exit_price = position.stop_loss
                        hit = True
                    elif position.take_profit and price >= position.take_profit:
                        exit_price = position.take_profit
                        hit = True
                else:
                    if price >= position.stop_loss:
                        exit_price = position.stop_loss
                        hit = True
                    elif position.take_profit and price <= position.take_profit:
                        exit_price = position.take_profit
                        hit = True
                if hit:
                    comm = self._commission(position.quantity, exit_price)
                    net_exit = exit_price + (comm / position.quantity) if position.side == 'SELL' else exit_price - (comm / position.quantity)
                    if position.side == 'BUY':
                        pnl = (net_exit - position.entry_price) * position.quantity
                    else:
                        pnl = (position.entry_price - net_exit) * position.quantity
                    capital += pnl
                    trades.append({'date': date, 'symbol': symbol, 'action': 'STOP/TP', 'pnl': pnl})
                    position = None

        return pd.DataFrame(trades) if trades else pd.DataFrame()

    def run(self, symbols: List[str], start_date: str, end_date: str,
            initial_capital: float = 100000.0) -> Dict:
        results = {}
        for sym in symbols:
            trades = self.backtest_symbol(sym, start_date, end_date, initial_capital)
            if not trades.empty:
                total_pnl = trades['pnl'].sum()
                wins = (trades['pnl'] > 0).sum()
                win_rate = wins / len(trades) if len(trades) else 0
                results[sym] = {
                    'total_trades': len(trades),
                    'win_rate': win_rate,
                    'total_pnl': total_pnl,
                    'avg_pnl_per_trade': trades['pnl'].mean(),
                }
        return results