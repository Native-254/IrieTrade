# backtest/engine.py
"""
Portfolio‑level backtester that re‑uses the live engine's strategy classes,
signal resolver, risk management, and position sizing.  Simulates multi‑symbol,
multi‑strategy trading over a historical date range with configurable
slippage, commissions, and trailing stops.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from risk.manager import RiskManager
from risk.position_manager import Position, PositionManager
from strategies.mean_revisions import MeanReversion
from strategies.orb import OpeningRangeBreakout
from strategies.signals import Signal
from strategies.trend_following_long_only import TrendFollowingLongOnly
from strategies.trend_following_ls import TrendFollowingLS
from strategies.vwap_revisions import VWAPReversion
from utils.config import CONFIG
from utils.logger import log


class BacktestEngine:
    """Full‑featured backtester that mirrors the live TradingEngine."""

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        strategy_params: dict[str, dict] | None = None,
        risk_config: dict | None = None,
        execution_config: dict | None = None,
    ):
        self.initial_capital = initial_capital
        self.risk_config = risk_config or CONFIG.get("risk_management", {})
        self.execution_config = execution_config or CONFIG.get("execution", {})

        # ----- Strategy loading (same as live engine) -----
        intraday_params = CONFIG["strategies"]["parameters"].get("intraday", {})
        safe_strategy_params = strategy_params or {}
        self.strategies = []
        for strat_cfg in CONFIG["strategies"]["active"]:
            if not strat_cfg.get("enabled", False):
                continue
            name = strat_cfg["name"]
            params_key = name.lower().replace(" ", "_")
            if name == "TrendFollowingLongOnly":
                params_key = "trend_following_long_only"
            # Use passed‑in params if available, otherwise fall back to config
            params = safe_strategy_params.get(
                params_key,
                intraday_params.get(
                    params_key, CONFIG["strategies"]["parameters"].get(params_key, {})
                ),
            )
            if name in ("TrendFollowing", "TrendFollowingLS"):
                self.strategies.append(TrendFollowingLS(params))
            elif name == "TrendFollowingLongOnly":
                self.strategies.append(TrendFollowingLongOnly(params))
            elif name == "MeanReversion":
                self.strategies.append(MeanReversion(params))
            elif name == "VWAPReversion":
                self.strategies.append(VWAPReversion(params))
            elif name == "OpeningRangeBreakout":
                self.strategies.append(OpeningRangeBreakout(params))
            elif name == "Breakout":
                log.warning("Breakout strategy not implemented – skipping.")
            else:
                log.warning(f"Unknown strategy '{name}' – skipping.")

        self.trailing_stop_percent = 0.02

    @staticmethod
    def _to_float(value: Any) -> float:
        if hasattr(value, "item"):
            value = value.item()
        return float(value)

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

    def _kelly_fraction(self, trade_results: list[tuple[str, float]]) -> float:
        if len(trade_results) < 5:
            return 0.02
        wins = [r[1] for r in trade_results if r[0] == "win"]
        losses = [abs(r[1]) for r in trade_results if r[0] == "loss"]
        if not wins or not losses:
            return 0.02
        win_rate = len(wins) / len(trade_results)
        avg_win = np.mean(wins) if wins else 0.01
        avg_loss = np.mean(losses) if losses else 0.01
        if avg_loss == 0:
            return 0.02
        kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        return float(max(0.0, min(kelly * 0.5, 0.05)))

    def _resolve_signal(self, signals_set: set[Signal], current_side: str | None) -> str | None:
        """Position‑aware resolver – identical to live engine."""
        if current_side == "BUY":
            if Signal.EXIT_LONG in signals_set:
                return "SELL"
            if Signal.ENTER_SHORT in signals_set:
                return "SELL"
        elif current_side == "SELL":
            if Signal.EXIT_SHORT in signals_set:
                return "BUY_TO_COVER"
            if Signal.ENTER_LONG in signals_set:
                return "BUY_TO_COVER"
        else:
            if Signal.ENTER_LONG in signals_set and Signal.ENTER_SHORT in signals_set:
                return None
            if Signal.ENTER_LONG in signals_set:
                return "BUY"
            if Signal.ENTER_SHORT in signals_set:
                return "SELL_SHORT"
        return None

    # ------------------------------------------------------------------
    # Main backtest loop – runs over a bar‑by‑bar DataFrame
    # ------------------------------------------------------------------
    def run(
        self,
        symbols: list[str],
        data: dict[str, pd.DataFrame],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Run backtest over a portfolio of symbols."""
        # Slice data
        if start_date or end_date:
            for sym in symbols:
                df = data[sym]
                if start_date:
                    df = df.loc[start_date:]
                if end_date:
                    df = df.loc[:end_date]
                data[sym] = df

        # Find the common time index across all symbols
        all_indices = [df.index for df in data.values() if not df.empty]
        if not all_indices:
            return {"error": "No data for any symbol"}
        common_index = all_indices[0]
        for idx in all_indices[1:]:
            common_index = common_index.intersection(idx)
        common_index = common_index.sort_values()

        if len(common_index) < 50:
            return {"error": "Not enough common data points"}

        # ----- Initialise portfolio state -----
        pm = PositionManager()
        rm = RiskManager(self.initial_capital, position_manager=pm, broker_name="backtest")
        rm.config = self.risk_config

        capital = self.initial_capital
        trade_results: list[tuple[str, float]] = []
        trade_log: list[dict] = []
        equity_curve = [capital]
        equity_dates = [common_index[0]]

        ohlcv_windows: dict[str, list[dict]] = {sym: [] for sym in symbols}

        # ----- Bar‑by‑bar loop -----
        for i, bar_time in enumerate(common_index):
            for sym in symbols:
                df = data.get(sym)
                if df is None or bar_time not in df.index:
                    continue
                row = df.loc[bar_time]
                ohlcv_windows[sym].append({
                    "open": self._to_float(row["Open"]),
                    "high": self._to_float(row["High"]),
                    "low": self._to_float(row["Low"]),
                    "close": self._to_float(row["Close"]),
                    "volume": self._to_float(row["Volume"]),
                    "timestamp": bar_time,
                })

            # ---------- 1. Check stop‑losses & trailing stops ----------
            for sym, pos in list(pm.positions.items()):
                if sym not in data or bar_time not in data[sym].index:
                    continue
                price = self._to_float(data[sym].loc[bar_time, "Close"])

                if pos.side == "BUY":
                    new_stop = max(pos.stop_loss, price * (1 - self.trailing_stop_percent))
                else:
                    new_stop = min(pos.stop_loss, price * (1 + self.trailing_stop_percent))
                if abs(new_stop - pos.stop_loss) > 0.01:
                    pos.stop_loss = new_stop

                if (pos.side == "BUY" and price <= pos.stop_loss) or \
                   (pos.side == "SELL" and price >= pos.stop_loss):
                    action = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                    qty = pos.quantity
                    fill_price = self._apply_slippage(price, action)
                    comm = self._calculate_commission(qty, fill_price)
                    pnl = ((fill_price - pos.entry_price) * qty) if pos.side == "BUY" else \
                          ((pos.entry_price - fill_price) * qty)
                    pnl -= comm
                    capital += pnl
                    denom = pos.entry_price * qty if pos.entry_price else 1.0
                    pnl_frac = pnl / denom
                    trade_results.append(("win" if pnl > 0 else "loss", pnl_frac))
                    trade_log.append({
                        "timestamp": bar_time, "symbol": sym, "action": action,
                        "quantity": qty, "entry_price": pos.entry_price,
                        "exit_price": fill_price, "pnl": pnl, "side": pos.side,
                    })
                    pm.close_position(sym)

            # ---------- 2. Generate signals for each symbol ----------
            if i < 50:
                unrealised = 0.0
                for sym, pos in pm.positions.items():
                    if sym in data and bar_time in data[sym].index:
                        price = self._to_float(data[sym].loc[bar_time, "Close"])
                        if pos.side == "BUY":
                            unrealised += (price - pos.entry_price) * pos.quantity
                        else:
                            unrealised += (pos.entry_price - price) * pos.quantity
                total_equity = capital + unrealised
                equity_curve.append(total_equity)
                equity_dates.append(bar_time)
                continue

            df_signals: dict[str, pd.DataFrame] = {}
            for sym in symbols:
                if sym not in ohlcv_windows or len(ohlcv_windows[sym]) < 50:
                    continue
                df = pd.DataFrame(ohlcv_windows[sym])
                df.set_index("timestamp", inplace=True)
                df_signals[sym] = df

            # ---------- 3. Process signals and trade entries/exits ----------
            for sym in sorted(df_signals.keys()):
                df = df_signals[sym]
                if df.empty:
                    continue
                last_price = self._to_float(df["close"].iloc[-1])

                pos = pm.positions.get(sym)
                current_side = pos.side if pos else None

                signals_set: set[Signal] = set()
                for strat in self.strategies:
                    raw = strat.generate_signals(df).iloc[-1]
                    if isinstance(raw, str):
                        try:
                            raw = Signal(raw.upper())
                        except ValueError:
                            raw = Signal.HOLD
                    if raw != Signal.HOLD:
                        signals_set.add(raw)

                action = self._resolve_signal(signals_set, current_side)
                if action is None:
                    continue

                # ----- Execute action -----
                if action in ("SELL", "BUY_TO_COVER"):
                    if not pos:
                        continue
                    qty = pos.quantity
                    fill_price = self._apply_slippage(last_price, action)
                    comm = self._calculate_commission(qty, fill_price)
                    pnl = ((fill_price - pos.entry_price) * qty) if pos.side == "BUY" else \
                          ((pos.entry_price - fill_price) * qty)
                    pnl -= comm
                    capital += pnl
                    denom = pos.entry_price * qty if pos.entry_price else 1.0
                    pnl_frac = pnl / denom
                    trade_results.append(("win" if pnl > 0 else "loss", pnl_frac))
                    trade_log.append({
                        "timestamp": bar_time, "symbol": sym, "action": action,
                        "quantity": qty, "entry_price": pos.entry_price,
                        "exit_price": fill_price, "pnl": pnl, "side": pos.side,
                    })
                    pm.close_position(sym)

                elif action in ("BUY", "SELL_SHORT"):
                    atr_val = self._to_float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
                    vol_stop_mult = self.risk_config.get("volatility_stop_multiplier", 2.0)
                    if action == "BUY":
                        stop_loss = last_price - (atr_val * vol_stop_mult)
                    else:
                        stop_loss = last_price + (atr_val * vol_stop_mult)

                    kelly_frac = self._kelly_fraction(trade_results)
                    base_qty = self.strategies[0].calculate_position_size(
                        capital=capital,
                        risk_per_trade=kelly_frac,
                        entry_price=last_price,
                        stop_loss_price=stop_loss,
                    )
                    if base_qty <= 0:
                        continue

                    max_single = capital * self.risk_config.get("max_position_pct", 0.2)
                    max_gross = capital * self.risk_config.get("max_gross_exposure", 1.5)

                    current_gross = 0.0
                    for p in pm.positions.values():
                        p_price = last_price
                        if p.symbol in data and bar_time in data[p.symbol].index:
                            p_price = self._to_float(data[p.symbol].loc[bar_time, "Close"])
                        current_gross += p.quantity * p_price
                    proposed_notional = base_qty * last_price
                    new_gross = current_gross + proposed_notional
                    if new_gross > max_gross:
                        budget = max_gross - current_gross
                        if budget <= 0:
                            continue
                        scaled_qty = int(budget / last_price)
                        base_qty = min(base_qty, scaled_qty)

                    existing_notional = pos.quantity * last_price if pos else 0.0
                    new_single = existing_notional + base_qty * last_price
                    if new_single > max_single:
                        budget = max_single - existing_notional
                        if budget <= 0:
                            continue
                        scaled_qty = int(budget / last_price)
                        base_qty = min(base_qty, scaled_qty)

                    if base_qty <= 0:
                        continue

                    max_net = capital * self.risk_config.get("max_net_exposure", 1.0)
                    long_exp = 0.0
                    short_exp = 0.0
                    for p in pm.positions.values():
                        p_price = last_price
                        if p.symbol in data and bar_time in data[p.symbol].index:
                            p_price = self._to_float(data[p.symbol].loc[bar_time, "Close"])
                        if p.side == "BUY":
                            long_exp += p.quantity * p_price
                        else:
                            short_exp += p.quantity * p_price
                    current_net = long_exp - short_exp
                    if action == "BUY":
                        new_net = current_net + base_qty * last_price
                    else:
                        new_net = current_net - base_qty * last_price
                    if abs(new_net) > max_net:
                        continue

                    fill_price = self._apply_slippage(last_price, action)
                    comm = self._calculate_commission(base_qty, fill_price)
                    cost = base_qty * fill_price + (comm if action == "BUY" else -comm)
                    capital -= cost

                    side = "BUY" if action == "BUY" else "SELL"
                    pm.open_position(
                        Position(
                            symbol=sym,
                            side=side,
                            quantity=base_qty,
                            entry_price=fill_price,
                            stop_loss=stop_loss,
                            entry_time=bar_time,
                        )
                    )
                    trade_log.append({
                        "timestamp": bar_time, "symbol": sym, "action": action,
                        "quantity": base_qty, "entry_price": fill_price,
                        "exit_price": None, "pnl": None, "side": side,
                    })

            # ---------- 4. Record equity after all trades for this bar ----------
            unrealised = 0.0
            for sym, pos in pm.positions.items():
                if sym in data and bar_time in data[sym].index:
                    price = self._to_float(data[sym].loc[bar_time, "Close"])
                    if pos.side == "BUY":
                        unrealised += (price - pos.entry_price) * pos.quantity
                    else:
                        unrealised += (pos.entry_price - price) * pos.quantity
            total_equity = capital + unrealised
            equity_curve.append(total_equity)
            equity_dates.append(bar_time)

        # Build equity series
        equity_series = pd.Series(equity_curve, index=equity_dates)
        final_unrealised = 0.0
        for sym, pos in pm.positions.items():
            last_bar = common_index[-1]
            price = pos.entry_price
            if sym in data and last_bar in data[sym].index:
                price = self._to_float(data[sym].loc[last_bar, "Close"])
            if pos.side == "BUY":
                final_unrealised += (price - pos.entry_price) * pos.quantity
            else:
                final_unrealised += (pos.entry_price - price) * pos.quantity
        final_capital = capital + final_unrealised

        returns = equity_series.pct_change().dropna()
        total_return = (final_capital - self.initial_capital) / self.initial_capital
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0
        rolling_max = equity_series.expanding().max()
        drawdowns = (equity_series - rolling_max) / rolling_max
        max_drawdown = drawdowns.min()
        win_trades = [r for r in trade_results if r[0] == "win"]
        win_rate = len(win_trades) / len(trade_results) if trade_results else 0.0
        total_trades = len(trade_results)

        return {
            "equity_curve": equity_series,
            "returns": returns,
            "final_capital": final_capital,
            "total_return": total_return,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "trade_log": trade_log,
        }