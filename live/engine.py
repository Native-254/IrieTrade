# live/engine.py
import csv
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import schedule
import uvicorn
import yfinance as yf

from data.manager import DataManager
from execution.broker_manager import BrokerManager
from monitoring.api import app as api_app
from monitoring.api import set_trading_engine
from monitoring.discord_alerter import DiscordAlerter
from monitoring.email_alerter import EmailAlerter
from monitoring.telegram_alerter import TelegramAlerter
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


class TradingEngine:
    def __init__(self, config: dict | None = None):
        log.info("Initializing Trading Engine…")
        self.config = config or CONFIG
        self.data_manager = DataManager()

        self.broker_manager = BrokerManager(self.config)

        self.telegram = TelegramAlerter()
        self.discord = DiscordAlerter()
        self.email = EmailAlerter()

        # ---------- Per‑broker resources ----------
        self.risk_managers: dict[str, RiskManager] = {}
        self.position_managers: dict[str, PositionManager] = {}
        self.symbols_by_broker: dict[str, list[str]] = {}
        self.broker_latest_prices: dict[str, dict] = {}
        self.broker_last_logged_qty: dict[str, dict[str, int]] = {}

        for broker_name, broker in self.broker_manager.iterate_all():
            try:
                account = broker.get_account_info()
                capital = account["net_liquidation"]
            except Exception as e:  # noqa: BLE001
                log.warning(
                    f"Could not fetch account info for {broker_name}: {e}. Using default capital."
                )
                capital = 100000.0
            pm = PositionManager()
            rm = RiskManager(capital, position_manager=pm)
            self.risk_managers[broker_name] = rm
            self.position_managers[broker_name] = pm

            symbols = (
                self.config["trading"]
                .get("symbols_by_broker", {})
                .get(broker_name, self.config["trading"]["symbols"])
            )
            self.symbols_by_broker[broker_name] = symbols
            self.broker_latest_prices[broker_name] = {}
            self.broker_last_logged_qty[broker_name] = {}

        self.trade_results: list[tuple[str, float]] = []
        self.equity_history: list[tuple[datetime, float]] = []
        self.unrealized_pnl: float = 0.0
        self.realized_pnl: float = 0.0

        # Load strategies once (shared)
        intraday_params = self.config["strategies"]["parameters"].get("intraday", {})
        self.strategies = []
        for strat_config in self.config["strategies"]["active"]:
            if not strat_config.get("enabled", False):
                continue
            name = strat_config["name"]
            params_key = name.lower().replace(" ", "_")
            if name == "TrendFollowingLongOnly":
                params_key = "trend_following_long_only"
            params = intraday_params.get(
                params_key, self.config["strategies"]["parameters"].get(params_key, {})
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
        self.is_running = False
        log.success("Trading Engine initialized.")

    # ------------------------------------------------------------------
    # Teardown & Restart
    # ------------------------------------------------------------------
    def _teardown(self):
        """Cleanly tear down all engine resources before re-initialising."""
        log.info("Tearing down engine resources…")
        for broker_name, broker in self.broker_manager.iterate_all():
            try:
                broker.disconnect()
                log.info(f"Disconnected broker: {broker_name}")
            except Exception as e:  # noqa: BLE001
                log.warning(f"Error disconnecting broker {broker_name}: {e}")

        self.position_managers.clear()
        self.risk_managers.clear()
        schedule.clear()
        self.is_running = False
        self.trade_results.clear()
        self.equity_history.clear()
        self.symbols_by_broker.clear()
        self.broker_latest_prices.clear()
        self.broker_last_logged_qty.clear()
        log.info("Teardown complete. Engine is clean.")

    def restart_with_new_config(self, new_config):
        log.info("Restarting engine with new configuration…")
        self._teardown()
        self.__init__(config=new_config)

    # ------------------------------------------------------------------
    # Crypto data fetcher (uses ccxt broker directly)
    # ------------------------------------------------------------------
    def _get_crypto_data(self, broker, symbol: str, limit: int = 200) -> pd.DataFrame:
        """Fetch OHLCV from a ccxt broker for crypto pairs."""
        if not hasattr(broker, 'exchange') or broker.exchange is None:
            broker.connect()
        if broker.exchange is None:   # <-- explicit guard against None
            log.warning(f"Could not connect to broker for crypto data: {symbol}")
            return pd.DataFrame()
        try:
            ohlcv = broker.exchange.fetch_ohlcv(symbol, timeframe='15m', limit=limit)
            if not ohlcv:
                return pd.DataFrame()
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:  # noqa: BLE001
            log.warning(f"Failed to fetch crypto data for {symbol}: {e}")
            return pd.DataFrame()

    def _is_crypto(self, symbol: str) -> bool:
        """Heuristic: crypto pairs contain a '/'."""
        return '/' in symbol

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _apply_slippage(self, price: float, action: str) -> float:
        if not self.config["execution"].get("simulate_slippage", False):
            return price
        slippage = self.config["execution"].get("slippage_percent", 0.0005)
        return (
            price * (1 + slippage)
            if action in ("BUY", "BUY_TO_COVER")
            else price * (1 - slippage)
        )

    def _calculate_commission(self, quantity: int, price: float) -> float:
        if not self.config["execution"].get("simulate_commissions", False):
            return 0.0
        trade_value = quantity * price
        per_share = quantity * self.config["execution"]["commission_per_share"]
        minimum = self.config["execution"]["commission_min"]
        maximum = trade_value * self.config["execution"]["commission_max_pct"]
        return max(minimum, min(per_share, maximum))

    def _log_trade(
        self,
        symbol,
        action,
        quantity,
        entry_price,
        exit_price,
        pnl,
        side,
        strategy_name="",
    ):
        filepath = "logs/trades.csv"
        headers = [
            "timestamp",
            "symbol",
            "action",
            "quantity",
            "entry_price",
            "exit_price",
            "pnl",
            "side",
            "strategy",
        ]
        write_header = not os.path.exists(filepath)
        with open(filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "action": action,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "side": side,
                    "strategy": strategy_name,
                }
            )

    def _simulate_partial_fill(self, requested_qty: int) -> int:
        if not self.config["execution"].get("simulate_partial_fills", False):
            return requested_qty
        ratio = np.random.uniform(
            self.config["execution"].get("partial_fill_min_ratio", 0.8), 1.0
        )
        filled = int(requested_qty * ratio)
        log.info(f"Simulated partial fill: {filled}/{requested_qty}")
        return max(1, filled)

    def _check_shortable(self, broker, symbol: str, quantity: int) -> bool:
        if not self.config["execution"].get("short_availability_check", False):
            return True
        return broker.is_shortable(symbol, quantity)

    def _earnings_nearby(self, symbol: str) -> bool:
        # Crypto symbols don't have earnings; skip immediately
        if self._is_crypto(symbol):
            return False
        if not self.config["execution"].get("earnings_avoidance", False):
            return False
        try:
            ticker = yf.Ticker(symbol)
            ed = ticker.earnings_dates
            if ed is not None and not ed.empty:
                next_earnings = ed.index[0].to_pydatetime()
                now_utc = datetime.now(timezone.utc)
                days_until = (next_earnings.replace(tzinfo=timezone.utc) - now_utc).days
                return (
                    0
                    <= days_until
                    <= self.config["execution"].get("earnings_avoidance_days", 5)
                )
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not fetch earnings for {symbol}: {e}")
        return False

    def _check_net_exposure(
        self, rm, action: str, quantity: int, last_price: float, latest_prices: dict
    ) -> bool:
        max_net = self.config["risk_management"].get("max_net_exposure", 1.0)
        if max_net >= 999:
            return True
        notional = quantity * last_price
        if action == "BUY":
            delta_long, delta_short = notional, 0
        elif action == "SELL_SHORT":
            delta_long, delta_short = 0, notional
        else:
            return True
        current_net = rm.get_net_exposure(latest_prices)
        new_net = current_net + delta_long - delta_short
        if abs(new_net) > max_net * rm.current_capital:
            log.warning(
                f"Net exposure {new_net:.2f} exceeds limit {max_net * rm.current_capital:.2f}"
            )
            return False
        return True

    def kelly_fraction(self) -> float:
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
    # Per‑broker trade execution
    # ------------------------------------------------------------------
    def _place_trade(
        self,
        broker,
        pm,
        symbol: str,
        action: str,
        quantity: int,
        last_price: float,
        stop_loss: float,
        atr: float,
        vol_stop_mult: float,
    ) -> bool:
        if self._earnings_nearby(symbol):
            self.email.send_error_alert(f"Trade skipped for {symbol}: earnings nearby.")
            log.warning(f"Earnings nearby for {symbol}, trade blocked.")
            return False

        if action == "SELL_SHORT" and not self._check_shortable(
            broker, symbol, quantity
        ):
            self.email.send_error_alert(
                f"Short sale rejected for {symbol}: not enough shares"
            )
            return False

        slipped_price = self._apply_slippage(last_price, action)

        # ---------- ENTRY ----------
        if action in ("BUY", "SELL_SHORT"):
            filled_qty = self._simulate_partial_fill(quantity)
            if filled_qty <= 0:
                return False

            use_bracket = getattr(broker, "supports_bracket", True)

            if use_bracket:
                stop_loss_slipped = self._apply_slippage(
                    stop_loss, "SELL" if action == "BUY" else "BUY_TO_COVER"
                )
                tp_price = (
                    slipped_price + (atr * vol_stop_mult * 2)
                    if action == "BUY"
                    else slipped_price - (atr * vol_stop_mult * 2)
                )
                try:
                    broker.connect()
                    if action == "BUY":
                        order_id, stop_id = broker.place_bracket_long(
                            symbol,
                            filled_qty,
                            slipped_price,
                            stop_loss_slipped,
                            tp_price,
                        )
                    else:
                        order_id, stop_id = broker.place_bracket_short(
                            symbol,
                            filled_qty,
                            slipped_price,
                            stop_loss_slipped,
                            tp_price,
                        )
                    if not order_id:
                        log.error(f"Failed to place bracket order for {symbol}")
                        self.email.send_error_alert(
                            f"Trade failed for {symbol}: bracket order rejected"
                        )
                        return False

                    fill = broker.wait_for_fill(order_id)
                    if fill["status"] != "Filled" or fill["filled"] == 0:
                        log.error(f"Order not filled for {symbol}: {fill['status']}")
                        self.email.send_error_alert(
                            f"Trade failed for {symbol}: order not filled"
                        )
                        return False

                    filled_qty = fill["filled"]
                    avg_price = fill["avg_price"]
                    commission = self._calculate_commission(filled_qty, avg_price)
                    net_entry_price = (
                        avg_price + (commission / filled_qty)
                        if action == "BUY"
                        else avg_price - (commission / filled_qty)
                    )
                    safe_stop_id = stop_id if stop_id is not None else 0

                    pm.open_position(
                        Position(
                            symbol=symbol,
                            side="BUY" if action == "BUY" else "SELL",
                            quantity=filled_qty,
                            entry_price=net_entry_price,
                            stop_loss=stop_loss_slipped,
                            stop_order_id=safe_stop_id,
                            entry_time=datetime.now(timezone.utc),
                        )
                    )
                    self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                    log.success(
                        f"Filled {action} {filled_qty} {symbol} @ ${avg_price:.2f} (bracket)"
                    )
                    return True

                except NotImplementedError:
                    log.warning(
                        "Bracket orders not supported, falling back to plain order"
                    )
                    use_bracket = False
                except Exception as e:  # noqa: BLE001
                    log.exception(f"Entry execution error for {symbol}: {e}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: {e}")
                    return False
                finally:
                    broker.disconnect()

            # Plain market order
            if not use_bracket:
                try:
                    broker.connect()
                    order_result = broker.place_order(
                        symbol=symbol,
                        side=action,
                        quantity=filled_qty,
                        order_type="MKT",
                    )
                    if not order_result or order_result["status"] not in (
                        "Filled",
                        "closed",
                    ):
                        log.error(f"Plain order failed for {symbol}")
                        self.email.send_error_alert(
                            f"Trade failed for {symbol}: plain order rejected"
                        )
                        return False

                    fill = broker.wait_for_fill(order_result["order_id"])
                    if fill["status"] != "Filled" or fill["filled"] == 0:
                        log.error(f"Plain order not filled for {symbol}")
                        return False

                    filled_qty = fill["filled"]
                    avg_price = fill["avg_price"]
                    commission = self._calculate_commission(filled_qty, avg_price)
                    net_entry_price = (
                        avg_price + (commission / filled_qty)
                        if action == "BUY"
                        else avg_price - (commission / filled_qty)
                    )

                    pm.open_position(
                        Position(
                            symbol=symbol,
                            side="BUY" if action == "BUY" else "SELL",
                            quantity=filled_qty,
                            entry_price=net_entry_price,
                            stop_loss=stop_loss,
                            stop_order_id=0,
                            entry_time=datetime.now(timezone.utc),
                        )
                    )
                    self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                    log.success(
                        f"Filled {action} {filled_qty} {symbol} @ ${avg_price:.2f} (plain)"
                    )
                    return True

                except Exception as e:  # noqa: BLE001
                    log.exception(f"Entry error for {symbol}: {e}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: {e}")
                    return False
                finally:
                    broker.disconnect()

        # ---------- EXIT ----------
        else:
            pos = pm.positions.get(symbol)
            if not pos:
                log.warning(f"No internal position for {symbol}")
                self.email.send_error_alert(
                    f"Trade failed for {symbol}: no position to close"
                )
                return False

            filled_qty = self._simulate_partial_fill(quantity)
            filled_qty = min(filled_qty, pos.quantity)

            try:
                broker.connect()
                order_result = broker.place_order(
                    symbol=symbol, side=action, quantity=filled_qty, order_type="MKT"
                )
                if not order_result:
                    log.error(f"Failed to place closing order for {symbol}")
                    self.email.send_error_alert(
                        f"Trade failed for {symbol}: closing order rejected"
                    )
                    return False

                fill = broker.wait_for_fill(order_result["order_id"])
                if fill["status"] != "Filled" or fill["filled"] == 0:
                    log.error(
                        f"Closing order not filled for {symbol}: {fill['status']}"
                    )
                    self.email.send_error_alert(
                        f"Trade failed for {symbol}: closing order not filled"
                    )
                    return False

                filled_qty = fill["filled"]
                avg_price = fill["avg_price"]
                commission = self._calculate_commission(filled_qty, avg_price)
                net_close_price = (
                    avg_price - (commission / filled_qty)
                    if action == "SELL"
                    else avg_price + (commission / filled_qty)
                )

                pnl_frac = (
                    (net_close_price - pos.entry_price) / pos.entry_price
                    if pos.side == "BUY"
                    else (pos.entry_price - net_close_price) / pos.entry_price
                )
                self.trade_results.append(("win" if pnl_frac > 0 else "loss", pnl_frac))

                pnl_dollar = (
                    (net_close_price - pos.entry_price) * filled_qty
                    if pos.side == "BUY"
                    else (pos.entry_price - net_close_price) * filled_qty
                )
                self._log_trade(
                    symbol,
                    action,
                    filled_qty,
                    pos.entry_price,
                    net_close_price,
                    pnl_dollar,
                    pos.side,
                )

                if filled_qty >= pos.quantity:
                    pm.close_position(symbol)
                else:
                    pos.quantity -= filled_qty

                self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                log.success(
                    f"Closed {action} {filled_qty} {symbol} @ ${avg_price:.2f}, P&L {pnl_frac:.4%}"
                )
                return True

            except Exception as e:  # noqa: BLE001
                log.exception(f"Exit execution error for {symbol}: {e}")
                self.email.send_error_alert(f"Trade failed for {symbol}: {e}")
                return False
            finally:
                broker.disconnect()

        return False

    # ------------------------------------------------------------------
    # Main iteration loop – runs over all brokers
    # ------------------------------------------------------------------
    def run_iteration(self):
        log.info("--- Running iteration ---")
        combined_nav = 0.0
        all_latest_prices = {}

        for broker_name, broker in self.broker_manager.iterate_all():
            rm = self.risk_managers[broker_name]
            pm = self.position_managers[broker_name]
            symbols = self.symbols_by_broker[broker_name]
            last_logged_qty = self.broker_last_logged_qty.setdefault(broker_name, {})

            account = broker.get_account_info()
            capital = account["net_liquidation"]
            rm.update_portfolio(capital - rm.current_capital, 0)
            if not rm.can_trade():
                log.warning(f"Trading halted for {broker_name}")
                continue

            if broker_name == "ib":
                self._sync_positions_from_broker(broker, pm)

            for sym, pos in pm.positions.items():
                last_logged_qty[sym] = pos.quantity

            latest_prices = {}

            # Trailing stops & price collection
            now_utc = datetime.now(timezone.utc)
            for sym, pos in pm.positions.items():
                if self._is_crypto(sym):
                    df = self._get_crypto_data(broker, sym, limit=200)
                else:
                    df = self.data_manager.get_data(
                        sym,
                        start_date=(now_utc - timedelta(days=1)).strftime("%Y-%m-%d"),
                        end_date=now_utc.strftime("%Y-%m-%d"),
                        interval="15m",
                        force_refresh=True,
                    )
                if df.empty:
                    continue
                last_price = df["close"].iloc[-1]
                latest_prices[sym] = last_price

                if pos.side == "BUY":
                    new_stop = max(
                        pos.stop_loss, last_price * (1 - self.trailing_stop_percent)
                    )
                else:
                    new_stop = min(
                        pos.stop_loss, last_price * (1 + self.trailing_stop_percent)
                    )
                if abs(new_stop - pos.stop_loss) > 0.01:
                    pos.stop_loss = new_stop
                    log.info(
                        f"Updated trailing stop for {sym} ({broker_name}): {new_stop:.2f}"
                    )
                    if pos.stop_order_id:
                        new_id = broker.update_stop_order(pos.stop_order_id, new_stop)
                        if new_id:
                            pos.stop_order_id = new_id
                    else:
                        log.warning(f"No stop order ID for {sym} ({broker_name}).")

                if (pos.side == "BUY" and last_price <= pos.stop_loss) or (
                    pos.side == "SELL" and last_price >= pos.stop_loss
                ):
                    log.warning(
                        f"Stop-loss triggered for {sym} ({broker_name}). Broker will close."
                    )

            self._reconcile_and_log_closed_positions(pm, last_logged_qty, latest_prices)
            self.broker_latest_prices[broker_name] = latest_prices
            rm.recalc_open_risk(latest_prices)

            # ------------------------------------------------------------------
            # Signal generation & trade entry / exit (position‑aware resolver)
            # ------------------------------------------------------------------
            for symbol in symbols:
                # Determine current side from PositionManager
                pos = pm.positions.get(symbol)
                current_side = pos.side if pos else None

                if self._is_crypto(symbol):
                    df = self._get_crypto_data(broker, symbol, limit=200)
                else:
                    df = self.data_manager.get_data(
                        symbol,
                        start_date=(now_utc - timedelta(days=7)).strftime("%Y-%m-%d"),
                        end_date=now_utc.strftime("%Y-%m-%d"),
                        interval="15m",
                        force_refresh=True,
                    )
                if df.empty:
                    continue
                last_price = df["close"].iloc[-1]
                latest_prices[symbol] = last_price

                # Collect signals from all active strategies
                signals_to_resolve = []
                for strategy in self.strategies:
                    raw = strategy.generate_signals(df).iloc[-1]
                    if isinstance(raw, str):
                        try:
                            raw = Signal(raw.upper())
                        except ValueError:
                            raw = Signal.HOLD
                    signals_to_resolve.append(raw)
                signals_set = {
                    s
                    for s in signals_to_resolve
                    if isinstance(s, Signal) and s != Signal.HOLD
                }

                # ---------- Resolver (Position‑Aware, Conflict‑Aware) ----------
                action = None
                if current_side == "BUY":
                    if Signal.EXIT_LONG in signals_set:
                        action = "SELL"
                    elif Signal.ENTER_SHORT in signals_set:
                        action = "SELL"
                        log.info(
                            f"Conflict resolved for {symbol}: closing long before possible short entry."
                        )
                elif current_side == "SELL":
                    if Signal.EXIT_SHORT in signals_set:
                        action = "BUY_TO_COVER"
                    elif Signal.ENTER_LONG in signals_set:
                        action = "BUY_TO_COVER"
                        log.info(
                            f"Conflict resolved for {symbol}: covering short before possible long entry."
                        )
                else:  # flat
                    if (
                        Signal.ENTER_LONG in signals_set
                        and Signal.ENTER_SHORT in signals_set
                    ):
                        log.warning(
                            f"Conflicting ENTER_LONG & ENTER_SHORT for {symbol} while flat – no action."
                        )
                    elif Signal.ENTER_LONG in signals_set:
                        action = "BUY"
                    elif Signal.ENTER_SHORT in signals_set:
                        action = "SELL_SHORT"

                if action is None:
                    continue

                reasons = [s.name for s in signals_set]
                log.info(
                    f"Resolved {symbol} ({broker_name}): {reasons} -> {action} (current side={current_side})"
                )

                # ---------- Exit execution ----------
                if action in ("SELL", "BUY_TO_COVER"):
                    if not pos:
                        log.warning(
                            f"Exit signal for {symbol} but no position – skipping."
                        )
                        continue
                    quantity = pos.quantity
                    success = self._place_trade(
                        broker,
                        pm,
                        symbol,
                        action,
                        quantity,
                        last_price,
                        stop_loss=0.0,
                        atr=0.0,
                        vol_stop_mult=0.0,
                    )
                    if success:
                        log.success(
                            f"Exit executed: {action} {quantity} {symbol} on {broker_name}"
                        )
                        self.telegram.send_trade_alert(
                            symbol, action, quantity, last_price
                        )
                        self.discord.send_trade_alert(
                            symbol, action, quantity, last_price
                        )
                    continue

                # ---------- Entry execution (BUY, SELL_SHORT) ----------
                atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
                vol_stop_mult = self.config["risk_management"][
                    "volatility_stop_multiplier"
                ]

                if action == "BUY":
                    stop_loss = last_price - (atr * vol_stop_mult)
                elif action == "SELL_SHORT":
                    stop_loss = last_price + (atr * vol_stop_mult)
                else:
                    continue

                quantity = self.strategies[0].calculate_position_size(
                    capital=capital,
                    risk_per_trade=self.kelly_fraction(),
                    entry_price=last_price,
                    stop_loss_price=stop_loss,
                )
                if quantity == 0:
                    continue

                proposed_notional = quantity * last_price
                current_gross = rm.get_gross_exposure(latest_prices)
                new_gross = current_gross + proposed_notional
                max_gross = capital * self.config["risk_management"].get(
                    "max_gross_exposure", 1.5
                )
                if new_gross > max_gross:
                    log.warning(f"Gross exposure limit for {symbol}")
                    continue

                max_single = capital * self.config["risk_management"].get(
                    "max_position_pct", 0.2
                )
                existing_notional = rm.get_position_notional(symbol, last_price)
                new_single = existing_notional + proposed_notional
                if new_single > max_single:
                    log.warning(f"Single-name limit for {symbol}")
                    continue

                order_valid, msg = rm.validate_order(
                    symbol, action, quantity, last_price, stop_loss
                )
                if not order_valid:
                    log.warning(f"Order rejected: {msg}")
                    continue

                if not self._check_net_exposure(
                    rm, action, quantity, last_price, latest_prices
                ):
                    log.warning(f"Net exposure limit for {symbol}")
                    continue

                if self._earnings_nearby(symbol):
                    log.warning(f"Earnings nearby for {symbol}, skipping.")
                    continue

                success = self._place_trade(
                    broker,
                    pm,
                    symbol,
                    action,
                    quantity,
                    last_price,
                    stop_loss,
                    atr,
                    vol_stop_mult,
                )
                if success:
                    log.success(
                        f"LIVE PAPER ORDER: {action} {quantity} {symbol} on {broker_name}"
                    )
                    self.telegram.send_trade_alert(symbol, action, quantity, last_price)
                    self.discord.send_trade_alert(symbol, action, quantity, last_price)

            combined_nav += capital

        self.equity_history.append((datetime.now(timezone.utc), combined_nav))
        self.latest_prices = all_latest_prices

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _sync_positions_from_broker(self, broker, pm):
        if broker.__class__.__name__ != "IBBroker":
            return
        try:
            ib_positions = broker.get_positions()
            symbols_in_ib = {p["symbol"] for p in ib_positions}
            for sym in list(pm.positions.keys()):
                if sym not in symbols_in_ib:
                    pm.close_position(sym)
                    log.warning(f"Removed stale position {sym}")
            for ib_pos in ib_positions:
                sym = ib_pos["symbol"]
                qty = ib_pos["quantity"]
                avg_cost = ib_pos["avg_cost"]
                if qty == 0:
                    if sym in pm.positions:
                        pm.close_position(sym)
                    continue
                side = "BUY" if qty > 0 else "SELL"
                if sym not in pm.positions:
                    pm.open_position(
                        Position(
                            symbol=sym,
                            side=side,
                            quantity=abs(qty),
                            entry_price=avg_cost,
                            stop_loss=0.0,
                        )
                    )
                else:
                    pos = pm.positions[sym]
                    pos.quantity = abs(qty)
                    pos.entry_price = avg_cost
        except Exception as e:  # noqa: BLE001
            log.error(f"Position sync failed: {e}")

    def _reconcile_and_log_closed_positions(self, pm, last_logged_qty, latest_prices):
        for sym, last_qty in list(last_logged_qty.items()):
            if last_qty <= 0:
                continue
            pos = pm.positions.get(sym)
            current_qty = pos.quantity if pos else 0
            if current_qty == last_qty:
                continue
            exit_qty = last_qty - current_qty
            if exit_qty <= 0:
                continue
            exit_price = latest_prices.get(sym)
            if exit_price is None:
                last_logged_qty[sym] = current_qty
                continue
            if not pos:
                last_logged_qty[sym] = current_qty
                continue
            pnl_dollar = (
                ((exit_price - pos.entry_price) * exit_qty)
                if pos.side == "BUY"
                else ((pos.entry_price - exit_price) * exit_qty)
            )
            action = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
            self._log_trade(
                sym, action, exit_qty, pos.entry_price, exit_price, pnl_dollar, pos.side
            )
            last_logged_qty[sym] = current_qty
        for sym, pos in pm.positions.items():
            last_logged_qty[sym] = pos.quantity

    def start(self):
        self.is_running = True
        log.info(
            f"Starting {self.config['general']['bot_name']} Trading Bot (Multi-Platform)..."
        )

        schedule.every().hour.at(":01").do(self.run_iteration)
        schedule.every().day.at("00:01").do(self._reset_daily_pnl)

        api_port = self.config["monitoring"]["health_check_port"]
        set_trading_engine(self)
        api_thread = threading.Thread(
            target=lambda: uvicorn.run(
                api_app, host="0.0.0.0", port=api_port, log_level="warning"
            ),
            daemon=True,
        )
        api_thread.start()
        log.success(f"Dashboard available at http://localhost:{api_port}/dashboard")

        while self.is_running:
            schedule.run_pending()
            time.sleep(30)

        log.info("Trading bot stopped.")

    def _reset_daily_pnl(self):
        for rm in self.risk_managers.values():
            rm.reset_daily_pnl()


if __name__ == "__main__":
    engine = TradingEngine()
    try:
        engine.start()
    except KeyboardInterrupt:
        log.info("Shutdown signal received. Stopping bot...")
        engine.is_running = False