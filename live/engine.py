# live/engine.py
import time
import schedule
import threading
import numpy as np
import uvicorn
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from dataclasses import dataclass

from utils.config import CONFIG
from utils.logger import log
from data.manager import DataManager
from strategies.trend_following import TrendFollowingLS
from strategies.mean_revisions import MeanReversion
from risk.manager import RiskManager
from execution.ib_broker import IBBroker
from monitoring.telegram_alerter import TelegramAlerter
from monitoring.discord_alerter import DiscordAlerter
from monitoring.api import app as api_app, set_trading_engine

@dataclass
class Position:
    """Tracks an open position."""
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: int
    entry_price: float
    stop_loss: float
    entry_time: datetime

class TradingEngine:
    def __init__(self):
        log.info("Initializing Trading Engine...")
        self.config = CONFIG
        self.data_manager = DataManager()
        self.broker = IBBroker()
        self.telegram = TelegramAlerter()
        self.discord = DiscordAlerter()

        # Initialize with paper trading account value
        initial_capital = self.broker.get_account_info()['net_liquidation']
        self.risk_manager = RiskManager(initial_capital)

        # Track trade results for Kelly criterion
        self.trade_results: List[Tuple[str, float]] = []  # [(win|loss, return_fraction), ...]
        self.open_positions: Dict[str, Position] = {}  # {symbol: Position, ...}
        
        # Trailing stop percentage
        self.trailing_stop_percent = 0.02  # 2%

        # Load intraday parameters for strategies
        intraday_params = self.config['strategies']['parameters'].get('intraday', {})
        
        self.strategies = []
        for strat_config in self.config['strategies']['active']:
            if not strat_config['enabled']:
                continue
            name = strat_config['name']
            params_key = name.lower().replace(' ', '_')
            # Use intraday params if available, else fall back to regular params
            params = intraday_params.get(params_key, 
                                         self.config['strategies']['parameters'].get(params_key, {}))
            if name == 'TrendFollowing' or name == 'TrendFollowingLS':
                self.strategies.append(TrendFollowingLS(params))
            elif name == 'MeanReversion':
                self.strategies.append(MeanReversion(params))
            elif name == 'Breakout':
                log.warning('Breakout strategy referenced but no Breakout implementation found. Skipping.')
            else:
                log.warning(f"Unknown strategy '{name}' in config. Skipping.")

        self.symbols_to_trade = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA', 'AMZN', 'META', 'JPM', 'V', 'MA', 'PG', 'DIS', 'HD', 'BAC', 'VZ', 'ADBE', 'CMCSA', 'NFLX', 'INTC', 'CSCO', 'PFE', 'MRK', 'KO', 'PEP', 'WMT', 'CVX', 'XOM', 'T', 'UNH', 'COST', 'ORCL', 'ABT', 'CRM', 'NKE', 'MCD', 'IBM', 'LLY', 'MDT', 'BMY', 'AMGN', 'SBUX', 'QCOM', 'TXN', 'GILD', 'FISV', 'INTU', 'GE', 'BA', 'CAT', 'MMM', 'AXP', 'SPGI', 'DE', 'DUK', 'SO', 'NEE', 'EXC', 'AEP', 'ED', 'D', 'EIX', 'PEG', 'SRE', 'WEC', 'ES', 'CMS'] # Your watchlist
        self.is_running = False
        log.success("Trading Engine initialized.")

    def kelly_fraction(self) -> float:
        """
        Calculate Kelly fraction based on recent trade performance.
        Default to 2% if insufficient data.
        """
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
        
        # Kelly formula: f = (bp - q) / b, where b = odds, p = win rate, q = loss rate
        # Simplified: kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        
        # Use half-Kelly for safety
        safe_kelly = max(0.0, min(kelly * 0.5, 0.05))  # Cap at 5%
        log.debug(f"Kelly fraction: {safe_kelly:.4f} (win_rate={win_rate:.2f}, avg_win={avg_win:.4f}, avg_loss={avg_loss:.4f})")
        return float(safe_kelly)

    def run_iteration(self):
        """Single iteration of the main trading loop."""
        log.info(f"--- Running iteration at {datetime.now()} ---")

        # 1. Update Risk Manager with current capital
        account_info = self.broker.get_account_info()
        current_capital = account_info['net_liquidation']
        # Simplified P&L calculation for the loop
        pnl_change = current_capital - self.risk_manager.current_capital
        self.risk_manager.update_portfolio(pnl_change, 0) # 0 open_risk_change for now

        if not self.risk_manager.can_trade():
            log.warning("Trading halted by risk manager.")
            return

        # 2. For each symbol, generate signals
        for symbol in self.symbols_to_trade:
            try:
                # Fetch recent intraday data (e.g., last 7 days of 15-min bars)
                # Force refresh to get latest incomplete bars
                end_date = datetime.now()
                start_date = end_date - timedelta(days=7)
                df = self.data_manager.get_data(symbol,
                                                start_date.strftime('%Y-%m-%d'),
                                                end_date.strftime('%Y-%m-%d'),
                                                interval="15m",
                                                force_refresh=True)

                if df.empty:
                    continue

                last_price = df['close'].iloc[-1]

                # --- TRAILING STOPS & PARTIAL EXITS ---
                # Check if we have an open position in this symbol
                if symbol in self.open_positions:
                    pos = self.open_positions[symbol]
                    # Trailing stop logic
                    if pos.side == 'BUY':
                        # For long positions, update stop-loss upward
                        new_stop = max(pos.stop_loss, last_price * (1 - self.trailing_stop_percent))
                        if new_stop > pos.stop_loss:
                            pos.stop_loss = new_stop
                            log.info(f"Updated trailing stop for {symbol} (BUY): {pos.stop_loss:.2f}")
                    elif pos.side == 'SELL':
                        # For short positions, update stop-loss downward (toward lower price)
                        new_stop = min(pos.stop_loss, last_price * (1 + self.trailing_stop_percent))
                        if new_stop < pos.stop_loss:
                            pos.stop_loss = new_stop
                            log.info(f"Updated trailing stop for {symbol} (SELL): {pos.stop_loss:.2f}")
                    
                    # Check if stop-loss triggered
                    if (pos.side == 'BUY' and last_price <= pos.stop_loss) or \
                       (pos.side == 'SELL' and last_price >= pos.stop_loss):
                        pnl_frac = (last_price - pos.entry_price) / pos.entry_price if pos.side == 'BUY' \
                                   else (pos.entry_price - last_price) / pos.entry_price
                        self.trade_results.append(('loss' if pnl_frac < 0 else 'win', pnl_frac))
                        del self.open_positions[symbol]
                        log.warning(f"Stop-loss triggered for {symbol}. PnL: {pnl_frac:.4f}")
                        continue

                # 3. Run strategies
                for strategy in self.strategies:
                    signals = strategy.generate_signals(df)
                    latest_signal = signals.iloc[-1]

                    if latest_signal != 'HOLD':
                        log.info(f"Signal detected for {symbol}: {latest_signal} by {strategy.name}")

                        # Check for exit signals and handle partial exits
                        exit_signals = ('EXIT_LONG', 'SELL', 'EXIT_SHORT', 'BUY_TO_COVER')
                        is_exit_signal = str(latest_signal).upper() in exit_signals
                        
                        if is_exit_signal and symbol in self.open_positions:
                            # Partial exit: close 50% of position
                            pos = self.open_positions[symbol]
                            exit_qty = pos.quantity // 2
                            if exit_qty > 0:
                                pnl_frac = (last_price - pos.entry_price) / pos.entry_price if pos.side == 'BUY' \
                                           else (pos.entry_price - last_price) / pos.entry_price
                                self.trade_results.append(('win' if pnl_frac > 0 else 'loss', pnl_frac))
                                pos.quantity -= exit_qty
                                if pos.quantity == 0:
                                    del self.open_positions[symbol]
                                log.info(f"Partial exit: sold {exit_qty} of {symbol} at {last_price:.2f}. PnL: {pnl_frac:.4f}")
                                continue
                            else:
                                # Position already mostly closed
                                if symbol in self.open_positions:
                                    del self.open_positions[symbol]
                                continue

                        # 4. Calculate trade details
                        last_price = df['close'].iloc[-1]
                        # Simple ATR for stop-loss (using last 14 periods)
                        atr = (df['high'] - df['low']).rolling(14).mean().iloc[-1]
                        stop_loss = last_price - (atr * self.config['risk_management']['volatility_stop_multiplier']) if latest_signal == 'BUY' else last_price + (atr * self.config['risk_management']['volatility_stop_multiplier'])

                        # 5. Calculate position size using Kelly criterion
                        kelly_risk = self.kelly_fraction()
                        quantity = strategy.calculate_position_size(
                            capital=current_capital,
                            risk_per_trade=kelly_risk,
                            entry_price=last_price,
                            stop_loss_price=stop_loss
                        )
                        if quantity == 0:
                            log.warning(f"Position size for {symbol} is zero. Skipping.")
                            continue

                        # 6. Validate with Risk Manager
                        order_valid, msg = self.risk_manager.validate_order(
                            symbol, latest_signal, quantity, last_price, stop_loss
                        )
                        if not order_valid:
                            log.warning(f"Order rejected for {symbol}: {msg}")
                            continue

                        # 7. Execute Trade (Paper or Live)
                        if self.config['execution']['paper_trading']:
                            log.success(f"[PAPER] Would {latest_signal} {quantity} shares of {symbol} at {last_price:.2f}. Stop: {stop_loss:.2f}")
                            # Simulate an order for risk manager
                            trade_risk = quantity * abs(last_price - stop_loss)
                            self.risk_manager.update_portfolio(0, trade_risk) # P&L change 0 for now
                            # Track position
                            entry_side = 'BUY' if latest_signal == 'BUY' else 'SELL'
                            self.open_positions[symbol] = Position(
                                symbol=symbol,
                                side=entry_side,
                                quantity=quantity,
                                entry_price=last_price,
                                stop_loss=stop_loss,
                                entry_time=datetime.now()
                            )
                            self.telegram.send_trade_alert(symbol, latest_signal, quantity, last_price)
                            self.discord.send_trade_alert(symbol, latest_signal, quantity, last_price)
                        else:
                            # --- LIVE TRADING ---
                            try:
                                self.broker.connect()
                                # Handle short-entry bracket orders explicitly
                                short_signals = ('SELL_SHORT', 'ENTER_SHORT', 'SHORT', 'SELL')
                                if str(latest_signal).upper() in short_signals:
                                    vol_stop_mult = self.config['risk_management']['volatility_stop_multiplier']
                                    tp_price = last_price - (atr * vol_stop_mult * 2)  # 2:1 RR
                                    order_id = self.broker.place_bracket_short(
                                        symbol, quantity, last_price, stop_loss, tp_price
                                    )
                                    if order_id:
                                        trade_risk = quantity * abs(last_price - stop_loss)
                                        self.risk_manager.update_portfolio(0, trade_risk)
                                        # Track short position
                                        self.open_positions[symbol] = Position(
                                            symbol=symbol,
                                            side='SELL',
                                            quantity=quantity,
                                            entry_price=last_price,
                                            stop_loss=stop_loss,
                                            entry_time=datetime.now()
                                        )
                                        self.telegram.send_trade_alert(symbol, 'SELL_SHORT', quantity, last_price)
                                        log.success(f"Bracket short placed for {symbol}. Parent order id: {order_id}")
                                    else:
                                        log.error(f"Failed to place bracket short for {symbol}.")
                                        self.telegram.send_error_alert(f"Failed to place bracket short for {symbol}.")
                                        self.discord.send_error_alert(f"Failed to place bracket short for {symbol}.")
                                else:
                                    order_result = self.broker.place_order(
                                        symbol=symbol,
                                        side=latest_signal,
                                        quantity=quantity,
                                        order_type='MKT'
                                    )
                                    if order_result and order_result['status'] == 'Filled':
                                        # Update risk manager with the actual trade
                                        trade_risk = quantity * abs(last_price - stop_loss)
                                        self.risk_manager.update_portfolio(0, trade_risk)
                                        # Track position
                                        entry_side = 'BUY' if latest_signal == 'BUY' else 'SELL'
                                        self.open_positions[symbol] = Position(
                                            symbol=symbol,
                                            side=entry_side,
                                            quantity=quantity,
                                            entry_price=last_price,
                                            stop_loss=stop_loss,
                                            entry_time=datetime.now()
                                        )
                                        self.telegram.send_trade_alert(symbol, latest_signal, quantity, order_result['avg_price'])
                                        log.success(f"LIVE ORDER EXECUTED: {order_result}")
                                    else:
                                        log.error(f"Live order failed for {symbol}. Status: {order_result}")
                                        self.telegram.send_error_alert(f"Live order failed for {symbol}.")
                                        self.discord.send_error_alert(f"Live order failed for {symbol}.")
                            except Exception as e:
                                log.exception(f"Critical error placing live order for {symbol}: {e}")
                                self.telegram.send_error_alert(f"Live order exception for {symbol}: {e}")
                                self.discord.send_error_alert(f"Live order exception for {symbol}: {e}")
                            finally:
                                self.broker.disconnect()

            except Exception as e:
                log.error(f"Error processing {symbol}: {e}")
                self.telegram.send_error_alert(f"Error processing {symbol}: {e}")
                self.discord.send_error_alert(f"Error processing {symbol}: {e}")

        # Reset simulated open risk at the end of each paper trading iteration
        if self.config['execution']['paper_trading']:
            self.risk_manager.open_risk = 0.0

    def start(self):
        """Starts the main trading loop."""
        self.is_running = True
        log.info(f"Starting {self.config['general']['bot_name']} Trading Bot...")

        # Schedule the main loop to run every hour at :01 (1 minute after each hour)
        # This allows fresh data for intraday strategies
        schedule.every().hour.at(":01").do(self.run_iteration)

        # Schedule a daily reset for the risk manager's P&L
        schedule.every().day.at("00:01").do(self.risk_manager.reset_daily_pnl)
        # Start the FastAPI dashboard in a background thread
        api_port = self.config['monitoring']['health_check_port']
        set_trading_engine(self)
        api_thread = threading.Thread(
            target=lambda: uvicorn.run(api_app, host="0.0.0.0", port=api_port, log_level="warning"),
            daemon=True
        )
        api_thread.start()
        log.success(f"Dashboard available at http://localhost:{api_port}/dashboard")


        # Run the main loop
        while self.is_running:
            schedule.run_pending()
            time.sleep(30) # Check every 30 seconds

        log.info("Trading bot stopped.")

if __name__ == "__main__":
    engine = TradingEngine()
    try:
        engine.start()
    except KeyboardInterrupt:
        log.info("Shutdown signal received. Stopping bot...")
        engine.is_running = False