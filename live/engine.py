# live/engine.py
import time
import schedule
import threading
import numpy as np
import uvicorn
import yfinance as yf
from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple

from utils.config import CONFIG
from utils.logger import log
from data.manager import DataManager
from strategies.trend_following import TrendFollowingLS
from strategies.mean_revisions import MeanReversion
from strategies.signals import Signal
from risk.manager import RiskManager
from risk.position_manager import PositionManager, Position
from execution.ib_broker import IBBroker
from monitoring.telegram_alerter import TelegramAlerter
from monitoring.discord_alerter import DiscordAlerter
from monitoring.email_alerter import EmailAlerter
from monitoring.api import app as api_app, set_trading_engine

class TradingEngine:
    def __init__(self, broker: Optional[Any] = None, data_manager: Optional[Any] = None,
                 telegram: Optional[Any] = None, discord: Optional[Any] = None,
                 email: Optional[Any] = None, position_manager: Optional[Any] = None,
                 risk_manager: Optional[Any] = None, config: Optional[dict] = None):
        log.info("Initializing Trading Engine...")
        self.config = config if config is not None else CONFIG
        self.data_manager = data_manager or DataManager()
        self.broker = broker or IBBroker()
        self.telegram = telegram or TelegramAlerter()
        self.discord = discord or DiscordAlerter()
        self.email = email or EmailAlerter()

        initial_capital = self.broker.get_account_info()['net_liquidation']
        self.position_manager = position_manager or PositionManager()
        self.risk_manager = risk_manager or RiskManager(initial_capital, position_manager=self.position_manager)

        self.trade_results: List[Tuple[str, float]] = []
        self.open_positions = self.position_manager.positions

        self.trailing_stop_percent = 0.02
        self.equity_history: List[Tuple[datetime, float]] = []
        self.latest_prices: dict = {}
        self.last_account_info: dict = {}
        self.unrealized_pnl: float = 0.0
        self.realized_pnl: float = 0.0

        intraday_params = self.config['strategies']['parameters'].get('intraday', {})
        self.strategies = []
        for strat_config in self.config['strategies']['active']:
            if not strat_config['enabled']:
                continue
            name = strat_config['name']
            params_key = name.lower().replace(' ', '_')
            params = intraday_params.get(params_key,
                                         self.config['strategies']['parameters'].get(params_key, {}))
            if name in ('TrendFollowing', 'TrendFollowingLS'):
                self.strategies.append(TrendFollowingLS(params))
            elif name == 'MeanReversion':
                self.strategies.append(MeanReversion(params))
            elif name == 'Breakout':
                log.warning('Breakout strategy referenced but not implemented. Skipping.')
            else:
                log.warning(f"Unknown strategy '{name}' in config. Skipping.")

        self.symbols_to_trade = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META', 'JPM', 'V', 'MA', 'PG', 'DIS', 'HD', 'BAC', 'VZ', 'ADBE', 'CMCSA', 'NFLX', 'INTC', 'CSCO', 'PFE', 'MRK', 'KO', 'PEP', 'WMT', 'CVX', 'XOM', 'T', 'UNH', 'COST', 'ORCL', 'ABT', 'CRM', 'NKE', 'MCD', 'IBM', 'LLY', 'MDT', 'BMY', 'AMGN', 'SBUX', 'QCOM', 'TXN', 'GILD', 'FISV', 'INTU', 'GE', 'BA', 'CAT', 'MMM', 'AXP', 'SPGI', 'DE', 'DUK', 'SO', 'NEE', 'EXC', 'AEP', 'ED', 'D', 'EIX', 'PEG', 'SRE', 'WEC', 'ES', 'CMS','VTI', 'QQQM', 'SMH', 'FDVV', 'FTEC', 'VWO', 'VOO', 'SCHM', 'QQQ', 'SCHA', 'SCHD', 'VGT']
        self.is_running = False
        log.success("Trading Engine initialized.")

    def _apply_slippage(self, price: float, action: str) -> float:
        if not self.config['execution'].get('simulate_slippage', False):
            return price
        slippage = self.config['execution'].get('slippage_percent', 0.0005)
        if action in ('BUY', 'BUY_TO_COVER'):
            return price * (1 + slippage)
        else:
            return price * (1 - slippage)

    def _calculate_commission(self, quantity: int, price: float) -> float:
        if not self.config['execution'].get('simulate_commissions', False):
            return 0.0
        trade_value = quantity * price
        per_share = quantity * self.config['execution']['commission_per_share']
        minimum = self.config['execution']['commission_min']
        maximum = trade_value * self.config['execution']['commission_max_pct']
        return max(minimum, min(per_share, maximum))

    def _simulate_partial_fill(self, requested_qty: int) -> int:
        if not self.config['execution'].get('simulate_partial_fills', False):
            return requested_qty
        ratio = np.random.uniform(
            self.config['execution'].get('partial_fill_min_ratio', 0.8), 1.0
        )
        filled = int(requested_qty * ratio)
        log.info(f"Simulated partial fill: {filled}/{requested_qty}")
        return max(1, filled)

    def _check_shortable(self, symbol: str, quantity: int) -> bool:
        if not self.config['execution'].get('short_availability_check', False):
            return True
        return self.broker.is_shortable(symbol, quantity)

    def _earnings_nearby(self, symbol: str) -> bool:
        if not self.config['execution'].get('earnings_avoidance', False):
            return False
        try:
            ticker = yf.Ticker(symbol)
            ed = ticker.earnings_dates
            if ed is not None and not ed.empty:
                next_earnings = ed.index[0].to_pydatetime()
                days_until = (next_earnings - datetime.now()).days
                return 0 <= days_until <= self.config['execution'].get('earnings_avoidance_days', 5)
        except Exception as e:
            log.debug(f"Could not fetch earnings for {symbol}: {e}")
        return False

    def _check_net_exposure(self, action: str, symbol: str, quantity: int,
                            last_price: float, latest_prices: dict) -> bool:
        max_net = self.config['risk_management'].get('max_net_exposure', 1.0)
        if max_net >= 999:
            return True
        notional = quantity * last_price
        if action == 'BUY':
            delta_long = notional
            delta_short = 0
        elif action == 'SELL_SHORT':
            delta_long = 0
            delta_short = notional
        else:
            return True
        current_net = self.risk_manager.get_net_exposure(latest_prices)
        new_net = current_net + delta_long - delta_short
        if abs(new_net) > max_net * self.risk_manager.current_capital:
            log.warning(f"Net exposure {new_net:.2f} would exceed limit {max_net*self.risk_manager.current_capital:.2f}")
            return False
        return True

    def kelly_fraction(self) -> float:
        if not self.trade_results or len(self.trade_results) < 5:
            return 0.02
        wins = [r[1] for r in self.trade_results if r[0] == 'win']
        losses = [abs(r[1]) for r in self.trade_results if r[0] == 'loss']
        if not wins or not losses:
            return 0.02
        win_rate = len(wins) / len(self.trade_results)
        avg_win = np.mean(wins) if wins else 0.01
        avg_loss = np.mean(losses) if losses else 0.01
        if avg_loss == 0:
            return 0.02
        kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        safe_kelly = max(0.0, min(kelly * 0.5, 0.05))
        return float(safe_kelly)

    def _sync_positions_from_broker(self):
        try:
            ib_positions = self.broker.get_positions()
            symbols_in_ib = {p['symbol'] for p in ib_positions}
            for sym in list(self.position_manager.positions.keys()):
                if sym not in symbols_in_ib:
                    self.position_manager.close_position(sym)
                    log.warning(f"Removed stale position for {sym}")
            for ib_pos in ib_positions:
                sym = ib_pos['symbol']
                qty = ib_pos['quantity']
                avg_cost = ib_pos['avg_cost']
                if qty == 0:
                    if sym in self.position_manager.positions:
                        self.position_manager.close_position(sym)
                    continue
                side = 'BUY' if qty > 0 else 'SELL'
                if sym not in self.position_manager.positions:
                    self.position_manager.open_position(Position(
                        symbol=sym, side=side, quantity=abs(qty),
                        entry_price=avg_cost, stop_loss=0.0
                    ))
                else:
                    pos = self.position_manager.positions[sym]
                    pos.quantity = abs(qty)
                    pos.entry_price = avg_cost
        except Exception as e:
            log.error(f"Failed to sync positions: {e}")

    def _place_trade(self, symbol: str, action: str, quantity: int,
                 last_price: float, stop_loss: float,
                 atr: float, vol_stop_mult: float) -> bool:
        if self._earnings_nearby(symbol):
            self.email.send_error_alert(
                f"Trade skipped for {symbol}: earnings within {self.config['execution']['earnings_avoidance_days']} days."
            )
            log.warning(f"Earnings nearby for {symbol}, trade blocked in _place_trade.")
            return False

        if action == 'SELL_SHORT' and not self._check_shortable(symbol, quantity):
            self.email.send_error_alert(f"Short sale rejected for {symbol}: not enough shares")
            return False

        slipped_price = self._apply_slippage(last_price, action)

        if action in ('BUY', 'SELL_SHORT'):
            filled_qty = self._simulate_partial_fill(quantity)
            if filled_qty <= 0:
                return False

            stop_loss = self._apply_slippage(stop_loss, 'SELL' if action == 'BUY' else 'BUY_TO_COVER')
            tp_price = slipped_price + (atr * vol_stop_mult * 2) if action == 'BUY' else slipped_price - (atr * vol_stop_mult * 2)

            try:
                self.broker.connect()
                if action == 'BUY':
                    order_id, stop_id = self.broker.place_bracket_long(
                        symbol, filled_qty, slipped_price, stop_loss, tp_price
                    )
                else:
                    order_id, stop_id = self.broker.place_bracket_short(
                        symbol, filled_qty, slipped_price, stop_loss, tp_price
                    )
                if not order_id:
                    log.error(f"Failed to place bracket order for {symbol}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: bracket order rejected")
                    return False

                fill = self.broker.wait_for_fill(order_id)
                if fill['status'] != 'Filled' or fill['filled'] == 0:
                    log.error(f"Order not filled for {symbol}: {fill['status']}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: order not filled ({fill['status']})")
                    return False

                filled_qty = fill['filled']
                avg_price = fill['avg_price']

                commission = self._calculate_commission(filled_qty, avg_price)
                net_entry_price = avg_price + (commission / filled_qty) if action == 'BUY' else avg_price - (commission / filled_qty)

                # Ensure stop_id is an integer, default 0 if None
                safe_stop_id = stop_id if stop_id is not None else 0

                self.position_manager.open_position(Position(
                    symbol=symbol,
                    side='BUY' if action == 'BUY' else 'SELL',
                    quantity=filled_qty,
                    entry_price=net_entry_price,
                    stop_loss=stop_loss,
                    stop_order_id=safe_stop_id,
                    entry_time=datetime.now()
                ))

                self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                log.success(f"Filled {action} {filled_qty} {symbol} @ ${avg_price:.2f} (slipped from {last_price:.2f}, net cost {net_entry_price:.2f})")
                return True

            except Exception as e:
                log.exception(f"Entry execution error for {symbol}: {e}")
                self.email.send_error_alert(f"Trade failed for {symbol}: {e}")
                return False
            finally:
                self.broker.disconnect()

        else:
            pos = self.position_manager.positions.get(symbol)
            if not pos:
                log.warning(f"No internal position for {symbol}")
                self.email.send_error_alert(f"Trade failed for {symbol}: no position to close")
                return False

            filled_qty = self._simulate_partial_fill(quantity)
            filled_qty = min(filled_qty, pos.quantity)

            try:
                self.broker.connect()
                order_result = self.broker.place_order(
                    symbol=symbol,
                    side=action,
                    quantity=filled_qty,
                    order_type='MKT'
                )
                if not order_result:
                    log.error(f"Failed to place closing order for {symbol}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: closing order rejected")
                    return False

                fill = self.broker.wait_for_fill(order_result['order_id'])
                if fill['status'] != 'Filled' or fill['filled'] == 0:
                    log.error(f"Closing order not filled for {symbol}: {fill['status']}")
                    self.email.send_error_alert(f"Trade failed for {symbol}: closing order not filled")
                    return False

                filled_qty = fill['filled']
                avg_price = fill['avg_price']
                commission = self._calculate_commission(filled_qty, avg_price)
                net_close_price = avg_price - (commission / filled_qty) if action == 'SELL' else avg_price + (commission / filled_qty)

                pnl_frac = (net_close_price - pos.entry_price) / pos.entry_price if pos.side == 'BUY' \
                           else (pos.entry_price - net_close_price) / pos.entry_price
                self.trade_results.append(('win' if pnl_frac > 0 else 'loss', pnl_frac))

                if filled_qty >= pos.quantity:
                    self.position_manager.close_position(symbol)
                else:
                    pos.quantity -= filled_qty

                self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                log.success(f"Closed {action} {filled_qty} {symbol} @ ${avg_price:.2f}, P&L {pnl_frac:.4%}")
                return True

            except Exception as e:
                log.exception(f"Exit execution error for {symbol}: {e}")
                self.email.send_error_alert(f"Trade failed for {symbol}: {e}")
                return False
            finally:
                self.broker.disconnect()

    def run_iteration(self):
        log.info(f"--- Running iteration at {datetime.now()} ---")

        account_info = self.broker.get_account_info()
        self.last_account_info = account_info
        current_capital = account_info['net_liquidation']
        self.unrealized_pnl = float(account_info.get('UnrealizedPnL', 0.0))
        self.realized_pnl = float(account_info.get('RealizedPnL', 0.0))
        pnl_change = current_capital - self.risk_manager.current_capital
        self.risk_manager.update_portfolio(pnl_change, 0)

        if not self.risk_manager.can_trade():
            log.warning("Trading halted by risk manager.")
            return

        self._sync_positions_from_broker()

        latest_prices = {}
        for sym, pos in self.position_manager.positions.items():
            df = self.data_manager.get_data(sym,
                                            start_date=(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                                            end_date=datetime.now().strftime('%Y-%m-%d'),
                                            interval="15m", force_refresh=True)
            if df.empty:
                continue
            last_price = df['close'].iloc[-1]
            latest_prices[sym] = last_price

            if pos.side == 'BUY':
                new_stop = max(pos.stop_loss, last_price * (1 - self.trailing_stop_percent))
            else:
                new_stop = min(pos.stop_loss, last_price * (1 + self.trailing_stop_percent))
            if abs(new_stop - pos.stop_loss) > 0.01:
                pos.stop_loss = new_stop
                log.info(f"Updated trailing stop for {sym}: {new_stop:.2f}")
                if pos.stop_order_id:
                    new_id = self.broker.update_stop_order(pos.stop_order_id, new_stop)
                    if new_id:
                        pos.stop_order_id = new_id
                else:
                    log.warning(f"No stop order ID for {sym}, can't update at broker.")

            if (pos.side == 'BUY' and last_price <= pos.stop_loss) or \
               (pos.side == 'SELL' and last_price >= pos.stop_loss):
                log.warning(f"Stop-loss triggered for {sym}. Broker will close.")

        self.latest_prices = latest_prices
        self.risk_manager.recalc_open_risk(latest_prices)

        for symbol in self.symbols_to_trade:
            if symbol in self.position_manager.positions:
                continue

            df = self.data_manager.get_data(symbol,
                                            start_date=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),
                                            end_date=datetime.now().strftime('%Y-%m-%d'),
                                            interval="15m", force_refresh=True)
            if df.empty:
                continue
            last_price = df['close'].iloc[-1]
            latest_prices[symbol] = last_price

            signals_to_resolve = []
            for strategy in self.strategies:
                raw = strategy.generate_signals(df).iloc[-1]
                if isinstance(raw, str):
                    try:
                        raw = Signal(raw.upper())
                    except ValueError:
                        raw = Signal.HOLD
                signals_to_resolve.append(raw)

            current_side = None
            enter_long = Signal.ENTER_LONG in signals_to_resolve
            exit_long = Signal.EXIT_LONG in signals_to_resolve
            enter_short = Signal.ENTER_SHORT in signals_to_resolve
            exit_short = Signal.EXIT_SHORT in signals_to_resolve

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

            if action is None:
                continue

            reasons = []
            if enter_long:
                reasons.append('ENTER_LONG')
            if exit_long:
                reasons.append('EXIT_LONG')
            if enter_short:
                reasons.append('ENTER_SHORT')
            if exit_short:
                reasons.append('EXIT_SHORT')
            log.info(f"Resolved {symbol}: {reasons} → {action} (current side: {current_side})")

            atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
            vol_stop_mult = self.config['risk_management']['volatility_stop_multiplier']

            if action == 'BUY':
                stop_loss = last_price - (atr * vol_stop_mult)
                quantity = self.strategies[0].calculate_position_size(
                    capital=current_capital,
                    risk_per_trade=self.kelly_fraction(),
                    entry_price=last_price,
                    stop_loss_price=stop_loss
                )
            elif action == 'SELL_SHORT':
                stop_loss = last_price + (atr * vol_stop_mult)
                quantity = self.strategies[0].calculate_position_size(
                    capital=current_capital,
                    risk_per_trade=self.kelly_fraction(),
                    entry_price=last_price,
                    stop_loss_price=stop_loss
                )
            else:
                continue

            if quantity == 0:
                continue

            proposed_notional = quantity * last_price
            current_gross = self.risk_manager.get_gross_exposure(latest_prices)
            new_gross = current_gross + proposed_notional
            max_gross = current_capital * self.config['risk_management'].get('max_gross_exposure', 1.5)
            if new_gross > max_gross:
                log.warning(
                    f"Order rejected for {symbol}: gross exposure {new_gross:.2f} exceeds limit {max_gross:.2f}"
                )
                self.email.send_error_alert(
                    f"Order for {symbol} rejected: gross exposure limit reached."
                )
                continue

            max_single = current_capital * self.config['risk_management'].get('max_position_pct', 0.2)
            existing_notional = self.risk_manager.get_position_notional(symbol, last_price)
            new_single = existing_notional + proposed_notional
            if new_single > max_single:
                log.warning(
                    f"Order rejected for {symbol}: position notional {new_single:.2f} exceeds single-name limit {max_single:.2f}"
                )
                self.email.send_error_alert(
                    f"Order for {symbol} rejected: single-name position limit reached."
                )
                continue

            order_valid, msg = self.risk_manager.validate_order(
                symbol, action, quantity, last_price, stop_loss
            )
            if not order_valid:
                log.warning(f"Order rejected for {symbol}: {msg}")
                continue

            if not self._check_net_exposure(action, symbol, quantity, last_price, latest_prices):
                log.warning(f"Order rejected for {symbol} due to net exposure limit.")
                self.email.send_error_alert(
                    f"Order for {symbol} rejected: net exposure limit reached."
                )
                continue

            if self._earnings_nearby(symbol):
                log.warning(f"Earnings nearby for {symbol}, skipping trade.")
                self.email.send_error_alert(
                    f"Trade skipped for {symbol}: earnings within {self.config['execution']['earnings_avoidance_days']} days."
                )
                continue

            success = self._place_trade(symbol, action, quantity, last_price,
                                        stop_loss, atr, vol_stop_mult)
            if success:
                log.success(f"LIVE PAPER ORDER: {action} {quantity} {symbol}")
                self.telegram.send_trade_alert(symbol, action, quantity, last_price)
                self.discord.send_trade_alert(symbol, action, quantity, last_price)

        self.equity_history.append((datetime.now(), current_capital))
        self.latest_prices = latest_prices

    def start(self):
        self.is_running = True
        log.info(f"Starting {self.config['general']['bot_name']} Trading Bot (Live‑Paper Mode)…")

        schedule.every().hour.at(":01").do(self.run_iteration)
        schedule.every().day.at("00:01").do(self.risk_manager.reset_daily_pnl)

        api_port = self.config['monitoring']['health_check_port']
        set_trading_engine(self)
        api_thread = threading.Thread(
            target=lambda: uvicorn.run(api_app, host="0.0.0.0", port=api_port, log_level="warning"),
            daemon=True
        )
        api_thread.start()
        log.success(f"Dashboard available at http://localhost:{api_port}/dashboard")

        while self.is_running:
            schedule.run_pending()
            time.sleep(30)

        log.info("Trading bot stopped.")

if __name__ == "__main__":
    engine = TradingEngine()
    try:
        engine.start()
    except KeyboardInterrupt:
        log.info("Shutdown signal received. Stopping bot...")
        engine.is_running = False