# backtest/engine.py
"""
Loop‑based backtester that re‑uses the live engine's strategy classes and
signal‑resolver logic.  Simulates multi‑strategy, multi‑position trading
on a single symbol for a given date range, with configurable risk limits,
slippage, and commissions.
"""

import numpy as np
import pandas as pd

from risk.manager import RiskManager
from risk.position_manager import Position, PositionManager
from strategies.mean_revisions import MeanReversion
from strategies.signals import Signal
from strategies.trend_following_long_only import TrendFollowingLongOnly
from strategies.trend_following_ls import TrendFollowingLS
from utils.config import CONFIG


class BacktestEngine:
    """Vectorised signal generation + step‑by‑step portfolio simulation."""

    def __init__(
        self,
        strategy_params: dict | None = None,
        risk_config: dict | None = None,
        initial_capital: float = 1_000_000.0,
    ):
        self.initial_capital = initial_capital
        self.risk_config = risk_config or CONFIG.get("risk_management", {})
        self.execution_config = CONFIG.get("execution", {})

        # Build strategies (same as live engine)
        intraday_params = CONFIG["strategies"]["parameters"].get("intraday", {})
        self.strategies = []
        for strat_cfg in CONFIG["strategies"]["active"]:
            if not strat_cfg.get("enabled", False):
                continue
            name = strat_cfg["name"]
            params_key = name.lower().replace(" ", "_")
            if name == "TrendFollowingLongOnly":
                params_key = "trend_following_long_only"
            # Ensure params is always a dict
            params = (
                (strategy_params.get(params_key) if strategy_params else None)
                or intraday_params.get(params_key)
                or CONFIG["strategies"]["parameters"].get(params_key)
                or {}
            )
            if name in ("TrendFollowing", "TrendFollowingLS"):
                self.strategies.append(TrendFollowingLS(params))
            elif name == "TrendFollowingLongOnly":
                self.strategies.append(TrendFollowingLongOnly(params))
            elif name == "MeanReversion":
                self.strategies.append(MeanReversion(params))

        self.position_manager = PositionManager()
        self.risk_manager = RiskManager(
            initial_capital, position_manager=self.position_manager
        )
        self.trade_results = []  # (win/loss, pnl_frac)

        self.equity_curve = []
        self.trade_log = []

    # ------------------------------------------------------------------
    # Helpers (mirror live engine)
    # ------------------------------------------------------------------
    def _apply_slippage(self, price: float, action: str) -> float:
        if not self.execution_config.get("simulate_slippage", False):
            return price
        slip = self.execution_config.get("slippage_percent", 0.0005)
        return (
            price * (1 + slip)
            if action in ("BUY", "BUY_TO_COVER")
            else price * (1 - slip)
        )

    def _calculate_commission(self, quantity: int, price: float) -> float:
        if not self.execution_config.get("simulate_commissions", False):
            return 0.0
        trade_value = quantity * price
        per_share = quantity * self.execution_config["commission_per_share"]
        minimum = self.execution_config["commission_min"]
        maximum = trade_value * self.execution_config["commission_max_pct"]
        return max(minimum, min(per_share, maximum))

    def _kelly_fraction(self) -> float:
        if len(self.trade_results) < 5:
            return 0.02
        wins = [r[1] for r in self.trade_results if r[0] == "win"]
        losses = [abs(r[1]) for r in self.trade_results if r[0] == "loss"]
        if not wins or not losses:
            return 0.02
        win_rate = len(wins) / len(self.trade_results)
        avg_win = np.mean(wins) if wins else 0.01
        avg_loss = np.mean(losses) if losses else 0.01
        if avg_loss == 0:
            return 0.02
        kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        return float(max(0.0, min(kelly * 0.5, 0.05)))

    # ------------------------------------------------------------------
    # Resolver (exactly the same as updated live engine)
    # ------------------------------------------------------------------
    def _resolve_signal(self, signals_set: set, current_side: str | None):
        action = None
        if current_side == "BUY":
            if Signal.EXIT_LONG in signals_set:
                action = "SELL"
            elif Signal.ENTER_SHORT in signals_set:
                action = "SELL"  # close long first
        elif current_side == "SELL":
            if Signal.EXIT_SHORT in signals_set:
                action = "BUY_TO_COVER"
            elif Signal.ENTER_LONG in signals_set:
                action = "BUY_TO_COVER"  # cover short first
        else:
            if Signal.ENTER_LONG in signals_set and Signal.ENTER_SHORT in signals_set:
                pass  # conflict, no action
            elif Signal.ENTER_LONG in signals_set:
                action = "BUY"
            elif Signal.ENTER_SHORT in signals_set:
                action = "SELL_SHORT"
        return action

    # ------------------------------------------------------------------
    # Main backtest loop for one symbol
    # ------------------------------------------------------------------
    def run(
        self,
        symbol: str,
        df: pd.DataFrame,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """
        Parameters
        ----------
        symbol : str
        df : pd.DataFrame
            Must contain OHLCV columns and a datetime index.
        """
        if start_date:
            df = df.loc[start_date:]
        if end_date:
            df = df.loc[:end_date]
        if df.empty:
            return {"error": "No data"}

        self.position_manager = PositionManager()
        self.risk_manager = RiskManager(
            self.initial_capital, position_manager=self.position_manager
        )
        self.trade_results = []
        equity = [self.initial_capital]
        dates = [df.index[0]]
        capital = self.initial_capital

        for i in range(1, len(df)):
            date = df.index[i]
            price = df["close"].iloc[i]

            # Update trailing stops (simplified: check stop levels)
            for sym, pos in list(self.position_manager.positions.items()):
                if pos.side == "BUY":
                    new_stop = max(pos.stop_loss, price * 0.98)  # 2% trail
                else:
                    new_stop = min(pos.stop_loss, price * 1.02)
                if abs(new_stop - pos.stop_loss) > 0.01:
                    pos.stop_loss = new_stop

                # Check stop‑loss hit
                if (pos.side == "BUY" and price <= pos.stop_loss) or (
                    pos.side == "SELL" and price >= pos.stop_loss
                ):
                    pnl = (
                        (price - pos.entry_price) * pos.quantity
                        if pos.side == "BUY"
                        else (pos.entry_price - price) * pos.quantity
                    )
                    capital += pnl
                    self.trade_results.append(
                        (
                            "win" if pnl > 0 else "loss",
                            pnl / (pos.entry_price * pos.quantity),
                        )
                    )
                    self.trade_log.append(
                        (
                            date,
                            symbol,
                            "STOP_OUT",
                            pos.quantity,
                            pos.entry_price,
                            price,
                            pnl,
                        )
                    )
                    self.position_manager.close_position(sym)

            # Generate signals (only when we have enough data)
            if i < 50:
                equity.append(capital)
                dates.append(date)
                continue

            # Slice data up to current index
            window_df = df.iloc[: i + 1]
            signals = []
            for strat in self.strategies:
                raw = strat.generate_signals(window_df).iloc[-1]
                if isinstance(raw, str):
                    try:
                        raw = Signal(raw.upper())
                    except ValueError:
                        raw = Signal.HOLD
                signals.append(raw)
            signals_set = {s for s in signals if s != Signal.HOLD}

            # Determine current side
            pos = self.position_manager.positions.get(symbol)
            current_side = pos.side if pos else None

            action = self._resolve_signal(signals_set, current_side)

            if action is None:
                equity.append(capital)
                dates.append(date)
                continue

            # --- Exit actions ---
            if action in ("SELL", "BUY_TO_COVER"):
                if not pos:
                    equity.append(capital)
                    dates.append(date)
                    continue
                qty = pos.quantity
                # Execute exit with slippage & commission
                fill_price = self._apply_slippage(price, action)
                commission = self._calculate_commission(qty, fill_price)
                pnl = (
                    ((fill_price - pos.entry_price) * qty)
                    if pos.side == "BUY"
                    else ((pos.entry_price - fill_price) * qty)
                )
                pnl -= commission
                capital += pnl
                pnl_frac = pnl / (pos.entry_price * qty)
                self.trade_results.append(("win" if pnl > 0 else "loss", pnl_frac))
                self.trade_log.append(
                    (date, symbol, action, qty, pos.entry_price, fill_price, pnl)
                )
                self.position_manager.close_position(symbol)

            # --- Entry actions ---
            else:  # BUY or SELL_SHORT
                # Basic risk checks (simplified but respects config)
                atr = (window_df["high"] - window_df["low"]).rolling(14).mean().iloc[-1]
                vol_mult = self.risk_config.get("volatility_stop_multiplier", 2.0)
                stop = (
                    price - (atr * vol_mult)
                    if action == "BUY"
                    else price + (atr * vol_mult)
                )
                risk_per_share = abs(price - stop)
                if risk_per_share == 0:
                    continue
                kelly = self._kelly_fraction()
                quantity = int((capital * kelly) / risk_per_share)
                if quantity <= 0:
                    continue

                # Ensure notional doesn't exceed single-name limit
                max_single = capital * self.risk_config.get("max_position_pct", 0.2)
                if quantity * price > max_single:
                    quantity = int(max_single / price)
                if quantity <= 0:
                    continue

                # Execute entry
                fill_price = self._apply_slippage(price, action)
                commission = self._calculate_commission(quantity, fill_price)
                entry_cost = quantity * fill_price + commission
                capital -= entry_cost

                # Open position
                side = "BUY" if action == "BUY" else "SELL"
                self.position_manager.open_position(
                    Position(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=fill_price,
                        stop_loss=stop,
                        entry_time=date,
                    )
                )

            # Record equity (unrealized P&L)
            unrealized = 0.0
            for sym, p in self.position_manager.positions.items():
                if p.side == "BUY":
                    unrealized += (price - p.entry_price) * p.quantity
                else:
                    unrealized += (p.entry_price - price) * p.quantity
            total_equity = capital + unrealized
            equity.append(total_equity)
            dates.append(date)

        return {
            "equity_curve": pd.Series(equity, index=dates),
            "trades": self.trade_log,
            "final_capital": equity[-1],
            "total_trades": len(self.trade_log),
        }
