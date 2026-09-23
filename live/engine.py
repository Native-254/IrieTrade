import csv
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import schedule
import uvicorn
import yfinance as yf
from fastapi import FastAPI
from pydantic import BaseModel

from data.blotter import Blotter
from data.manager import DataManager
from execution.broker_manager import BrokerManager
from monitoring.api import app as api_app
from monitoring.api import set_trading_engine
from monitoring.discord_alerter import DiscordAlerter
from monitoring.email_alerter import EmailAlerter
from monitoring.telegram_alerter import TelegramAlerter
from risk.manager import RiskManager
from risk.position_manager import Position, PositionManager
from strategies.adx_filter import ADXTrendFilter
from strategies.aroon import AroonCross
from strategies.fibonacci import FibonacciRetracement
from strategies.ichimoku import IchimokuCloud
from strategies.macd_cross import MACDCross
from strategies.mean_revisions import MeanReversion
from strategies.obv_divergence import OBVDivergence
from strategies.orb import OpeningRangeBreakout
from strategies.signals import Signal
from strategies.sma_5_8_13 import SMA5813Strategy
from strategies.stochastic import StochasticCross
from strategies.talib_trend import TALibTrendStrategy
from strategies.trend_following_long_only import TrendFollowingLongOnly
from strategies.trend_following_ls import TrendFollowingLS
from strategies.vwap_revisions import VWAPReversion
from tools.african_history import AfricanHistoryStore
from tools.african_scanner import AfricanMarketScanner
from tools.nse_report import NSEReportGenerator
from tools.nse_scanner import NSEScanner
from tools.scanner import MarketScanner
from tools.sentiment_scanner import TrendingScanner
from utils.config import CONFIG
from utils.logger import log

START_TIME = time.time()
_engine_instance = None


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
        self.broker_last_logged_qty: dict[str, dict[str, float]] = {}
        self.broker_available: dict[str, bool] = {}
        self.symbol_cooldowns: dict[str, datetime] = {}

        for broker_name, broker in self.broker_manager.iterate_all():
            try:
                account = broker.get_account_info()
                capital = float(account["net_liquidation"])
                self.broker_available[broker_name] = True
            except Exception as e:  # noqa: BLE001
                log.warning(
                    f"Broker '{broker_name}' is not available: {e}. It will be skipped."
                )
                self.broker_available[broker_name] = False
                continue

            pm = PositionManager()
            rm = RiskManager(capital, position_manager=pm, broker_name=broker_name)
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

        if not self.risk_managers:
            log.error("No brokers available. Bot will idle until a broker connects.")

        self.trade_results: list[tuple[str, float]] = []
        self.strategy_performance: dict[str, list[float]] = defaultdict(list)
        self.equity_history: list[tuple[datetime, float]] = []
        self.unrealized_pnl: float = 0.0
        self.realized_pnl: float = 0.0
        self.latest_prices: dict[str, float] = {}
        self._dust_warned = set()
        self._crypto_fail_streak = 0

        # ──────────── Per‑broker strategy loading ────────────
        strategies_by_broker = self.config["strategies"].get("strategies_by_broker", {})
        intraday_params = self.config["strategies"]["parameters"].get("intraday", {})
        self.strategies_by_broker: dict[str, list] = {}

        for broker_name in self.risk_managers:
            strat_list = strategies_by_broker.get(
                broker_name, self.config["strategies"]["active"]
            )
            loaded = []
            for strat_config in strat_list:
                if not strat_config.get("enabled", False):
                    continue
                name = strat_config["name"]
                params_key = name.lower().replace(" ", "_")
                if name == "TrendFollowingLongOnly":
                    params_key = "trend_following_long_only"
                elif name == "SMA5813":
                    params_key = "sma_5_8_13"
                params = intraday_params.get(
                    params_key, self.config["strategies"]["parameters"].get(params_key, {})
                )
                if name in ("TrendFollowing", "TrendFollowingLS"):
                    loaded.append(TrendFollowingLS(params))
                elif name == "TrendFollowingLongOnly":
                    loaded.append(TrendFollowingLongOnly(params))
                elif name == "MeanReversion":
                    loaded.append(MeanReversion(params))
                elif name == "VWAPReversion":
                    loaded.append(VWAPReversion(params))
                elif name == "OpeningRangeBreakout":
                    loaded.append(OpeningRangeBreakout(params))
                elif name == "MACDCross":
                    loaded.append(MACDCross(params))
                elif name == "ADXTrendFilter":
                    loaded.append(ADXTrendFilter(params))
                elif name == "OBVDivergence":
                    loaded.append(OBVDivergence(params))
                elif name == "AroonCross":
                    loaded.append(AroonCross(params))
                elif name == "StochasticCross":
                    loaded.append(StochasticCross(params))
                elif name == "FibonacciRetracement":
                    loaded.append(FibonacciRetracement(params))
                elif name == "IchimokuCloud":
                    loaded.append(IchimokuCloud(params))
                elif name == "SMA5813":
                    loaded.append(SMA5813Strategy(params))
                elif name == "TALibTrend":
                    loaded.append(TALibTrendStrategy(params))
                elif name == "Breakout":
                    log.warning("Breakout strategy not implemented – skipping.")
                else:
                    log.warning(f"Unknown strategy '{name}' – skipping.")
            self.strategies_by_broker[broker_name] = loaded

        # Keep a default list for API endpoints etc.
        self.strategies = self.strategies_by_broker.get(
            next(iter(self.risk_managers.keys())) if self.risk_managers else "", []
        )

        # Scanner integration
        self.scanner_enabled = self.config.get("scanner", {}).get("enabled", False)
        self.scanner = MarketScanner() if self.scanner_enabled else None

        # NSE integration
        self.nse_enabled = self.config.get("nse", {}).get("enabled", False)
        if self.nse_enabled:
            self.nse_scanner = NSEScanner(self.config)
            api_key = os.getenv("AI_API_KEY")
            api_url = os.getenv("AI_API_URL")
            model = os.getenv("AI_MODEL")
            if api_key and api_url and model:
                self.nse_report_generator = NSEReportGenerator(
                    self.nse_scanner,
                    llm_api_key=api_key,
                    llm_api_url=api_url,
                    llm_model=model
                )
            else:
                self.nse_report_generator = None
                log.warning("NSE report generator not initialized: missing AI_API_KEY, AI_API_URL, or AI_MODEL")
        else:
            self.nse_scanner = None
            self.nse_report_generator = None

        # African market integration
        self.african_enabled = self.config.get("african_scanner", {}).get("enabled", False)
        if self.african_enabled:
            self.african_scanner = AfricanMarketScanner(self.config.get("african_scanner", {}))
            self.african_history = AfricanHistoryStore(self.african_scanner)
        else:
            self.african_scanner = None
            self.african_history = None

        self.trailing_stop_percent = 0.02
        self.is_running = False
        # Blotter for persistent OHLCV storage
        self.blotter = Blotter()

        # Read EOD and cap times from config (in UTC)
        risk_cfg = self.config.get("risk_management", {})
        self.eod_report_utc_time = risk_cfg.get("eod_report_utc_time", "19:55")
        self.overnight_cap_utc_time = risk_cfg.get("overnight_cap_utc_time", "19:50")
        self.weekend_flatten_utc_time = risk_cfg.get("weekend_flatten_utc_time", "19:50")

        # First-run welcome flag
        self.first_run = not Path("data/.welcome_shown").exists()
        if self.first_run:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/.welcome_shown").touch()
            log.info("First‑run welcome flag set – dashboard will show toast.")

        log.success("Trading Engine initialized.")
        global _engine_instance
        _engine_instance = self

    def _alert_if_critical(self, message: str, source: str = "", severity: str = "critical") -> None:
        """Send a critical alert via all available channels."""
        formatted = f"[{source}] {message}" if source else message
        try:
            self.email.send_error_alert(message, source, severity=severity)
        except Exception as e:  # noqa: BLE001
            log.debug(f"Failed to send email error alert: {e}")
        try:
            self.telegram.send_error_alert(formatted)
        except Exception as e:  # noqa: BLE001
            log.debug(f"Failed to send telegram error alert: {e}")
        try:
            self.discord.send_error_alert(formatted)
        except Exception as e:  # noqa: BLE001
            log.debug(f"Failed to send discord error alert: {e}")

    def _capture_market_data(self):
        """Capture market data from all brokers and store in blotter."""
        try:
            # Capture data for each broker
            for broker_name, broker in self.broker_manager.iterate_all():
                if not self.broker_available.get(broker_name, False):
                    continue

                # Get symbols for this broker
                symbols = self.symbols_by_broker.get(broker_name, [])
                if not symbols:
                    continue

                # Capture OHLCV data for each symbol
                for symbol in symbols:
                    try:
                        # Get data based on broker type
                        if self._is_crypto(symbol):
                            df = self._get_crypto_data(broker, symbol, limit=100)
                        else:
                            # For traditional assets, use yfinance or data manager
                            # For now, we'll use the data manager's get_bars method
                            df = self.data_manager.get_bars(symbol, count=100)

                        if not df.empty:
                            # Ensure required columns exist
                            required_cols = ['open', 'high', 'low', 'close', 'volume']
                            if all(col in df.columns for col in required_cols):
                                # Store in blotter with 15m timeframe
                                self.blotter.store_ohlcv(symbol, df, timeframe="15m")
                                log.debug(f"Stored {len(df)} bars for {symbol} in blotter")
                    except Exception as e:  # noqa: BLE001
                        log.debug(f"Failed to capture data for {symbol} on {broker_name}: {e}")
                        continue

            # Also capture data for African market symbols if enabled
            if self.african_enabled and self.african_scanner:
                try:
                    from datetime import datetime, timezone
                    # Capture African market snapshots (daily)
                    for exchange in ["NSE", "JSE", "NGX", "GSE", "BRVM"]:
                        try:
                            quotes = self.african_scanner.get_quotes(exchange)
                            for q in quotes:
                                symbol = q.get("symbol")
                                price = q.get("price")
                                if not symbol or price is None:
                                    continue
                                # Create a DataFrame for this symbol with one row (daily snapshot)
                                # We'll use the current date at midnight UTC as the timestamp
                                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                                df = pd.DataFrame({
                                    'open': [price],
                                    'high': [price],
                                    'low': [price],
                                    'close': [price],
                                    'volume': [q.get("volume", 0.0)]
                                }, index=[today])
                                # Store in blotter with timeframe="1d"
                                # Prefix with exchange to avoid symbol collisions across exchanges
                                self.blotter.store_ohlcv(f"{exchange}:{symbol}", df, timeframe="1d")
                                log.debug(f"Stored African snapshot for {exchange}:{symbol}")
                        except Exception as e:  # noqa: BLE001
                            log.debug(f"African capture failed for {exchange}: {e}")
                except Exception as e:  # noqa: BLE001
                    log.debug(f"Failed to capture African market data: {e}")

        except Exception as e:  # noqa: BLE001
            log.error(f"Error in _capture_market_data: {e}")

    def _get_bars(self, symbol: str, count: int = 100) -> pd.DataFrame:
        """
        Get OHLCV data for a symbol, preferring blotter data with fallback to live data.

        Args:
            symbol: Trading symbol
            count: Number of bars to return

        Returns:
            DataFrame with OHLCV data
        """
        # Try to get data from blotter first
        df = self.blotter.get_ohlcv(symbol, timeframe="15m", limit=count)

        # If we have sufficient data from blotter, use it
        if not df.empty and len(df) >= count * 0.8:  # At least 80% of requested data
            log.debug(f"Using blotter data for {symbol}: {len(df)} bars")
            return df

        # Fallback to live data
        log.debug(f"Falling back to live data for {symbol}")
        try:
            if self._is_crypto(symbol):
                # For crypto, we need to get from broker
                # Find a broker that has this symbol
                for broker_name, broker in self.broker_manager.iterate_all():
                    if self.broker_available.get(broker_name, False):
                        symbol_in_broker = any(symbol in s for s in self.symbols_by_broker.get(broker_name, []))
                        if symbol_in_broker:
                            df = self._get_crypto_data(broker, symbol, limit=count)
                            if not df.empty:
                                return df
            else:
                # For traditional assets, use data manager
                df = self.data_manager.get_bars(symbol, count=count)
                if not df.empty:
                    return df
        except Exception as e:  # noqa: BLE001
            log.debug(f"Failed to get live data for {symbol}: {e}")

        # If we got some blotter data but not enough, return what we have
        if not df.empty:
            return df

        # Return empty DataFrame if all else fails
        return pd.DataFrame()

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
        self.broker_available.clear()
        self.strategies_by_broker.clear()
        schedule.clear()
        self.is_running = False
        self.trade_results.clear()
        self.equity_history.clear()
        self.symbols_by_broker.clear()
        self.broker_latest_prices.clear()
        self.broker_last_logged_qty.clear()
        self.symbol_cooldowns.clear()
        log.info("Teardown complete. Engine is clean.")

    def restart_with_new_config(self, new_config):
        log.info("Restarting engine with new configuration…")
        self._teardown()
        self.__init__(config=new_config)

    # ------------------------------------------------------------------
    # Helper to identify broker source for error threading
    # ------------------------------------------------------------------
    def _get_broker_source(self, broker) -> str:
        """Return a short source label for the given broker instance."""
        name = broker.__class__.__name__
        if name == "IBBroker":
            return "ib"
        if name == "KucoinBroker":
            return "kucoin"
        if name == "BinanceBroker":
            return "binance"
        if name == "CoinbaseBroker":
            return "coinbase"
        if name == "DerivBroker":
            return "deriv"
        if name == "KrakenBroker":
            return "kraken"
        if name == "NSEBroker":
            return "nse"
        if name == "OKXBroker":
            return "okx"
        if name == "OlymptradeBroker":
            return "olymptrade"
        if name == "OneInchBroker":
            return "oneinch"
        if name == "Web3DexBroker":
            return "web3dex"
        return "general"

    # ------------------------------------------------------------------
    # Crypto data fetcher (uses ccxt broker directly)
    # ------------------------------------------------------------------
    def _get_crypto_data(self, broker, symbol: str, limit: int = 200) -> pd.DataFrame:
        """Fetch OHLCV from a ccxt broker for crypto pairs."""
        # Circuit breaker: after 3 consecutive network failures, stop trying
        # until the next iteration resets the counter.
        if getattr(self, "_crypto_fail_streak", 0) >= 3:
            return pd.DataFrame()

        if not hasattr(broker, "exchange") or broker.exchange is None:
            broker.connect()
        if broker.exchange is None:
            log.warning(f"Could not connect to broker for crypto data: {symbol}")
            return pd.DataFrame()

        # Retry-on-429 with backoff for rate limits
        for attempt in range(3):
            try:
                ohlcv = broker.exchange.fetch_ohlcv(symbol, timeframe='15m', limit=limit)
                if not ohlcv:
                    return pd.DataFrame()
                # Reset failure streak on success
                self._crypto_fail_streak = 0
                break
            except ccxt.RateLimitExceeded as e:
                if attempt == 2:  # Last attempt
                    log.warning(f"Failed to fetch crypto data for {symbol} after 3 attempts due to rate limit: {e}")
                    return pd.DataFrame()
                wait_time = 3 * (attempt + 1)
                log.warning(f"Rate limit exceeded for {symbol}, retrying in {wait_time}s... (attempt {attempt + 1}/3)")
                time.sleep(wait_time)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                self._crypto_fail_streak = getattr(self, "_crypto_fail_streak", 0) + 1
                log.warning(f"Failed to fetch crypto data for {symbol}: {e}")
                return pd.DataFrame()
            except Exception as e:  # noqa: BLE001
                log.warning(f"Failed to fetch crypto data for {symbol}: {e}")
                return pd.DataFrame()
        else:
            # This block runs if we didn't break out of the loop (all attempts failed)
            return pd.DataFrame()

        df = pd.DataFrame(
            ohlcv,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
        )
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        return df
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

    def _calculate_commission(self, quantity: float, price: float) -> float:
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
        strategy_name: str | None = None,
    ):
        if strategy_name is None:
            strategy_name = ""
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

        # Record performance for the strategy if a strategy name is provided
        if strategy_name and strategy_name.strip():
            trade_value = entry_price * quantity
            if trade_value != 0:
                pnl_frac = pnl / trade_value
                self.strategy_performance[strategy_name].append(pnl_frac)

    def _simulate_partial_fill(self, requested_qty: float) -> float:
        if not self.config["execution"].get("simulate_partial_fills", False):
            return requested_qty
        ratio = np.random.uniform(
            self.config["execution"].get("partial_fill_min_ratio", 0.8), 1.0
        )
        filled = (
            int(requested_qty * ratio)
            if requested_qty >= 1
            else requested_qty * ratio
        )
        log.info(f"Simulated partial fill: {filled}/{requested_qty}")
        return max(1, filled) if requested_qty >= 1 else filled

    def _check_shortable(self, broker, symbol: str, quantity: float) -> bool:
        # Always respect the broker's own capability first
        if not broker.is_shortable(symbol, quantity):
            return False
        # If the config says to skip the availability check, we're done
        if not self.config["execution"].get("short_availability_check", False):
            return True
        # Optional: further IBKR share-availability logic here
        return True

    def _earnings_nearby(self, symbol: str) -> bool:
        if self._is_crypto(symbol):
            return False
        # Forex and metals have no earnings — skip the lookup
        from data.symbol_map import is_mapped_derivative
        if is_mapped_derivative(symbol):
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
        self, rm, action: str, quantity: float, last_price: float, latest_prices: dict
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
        net_limit = max_net * rm.current_capital
        if abs(new_net) > net_limit:
            if abs(current_net) > net_limit and abs(new_net) < abs(current_net):
                log.info(
                    f"Allowing {action} because it reduces net exposure "
                    f"from {current_net:.2f} to {new_net:.2f}"
                )
                return True
            log.warning(
                f"Net exposure {new_net:.2f} exceeds limit {net_limit:.2f}"
            )
            return False
        return True

    def _get_min_order_notional(self, broker, symbol: str) -> float:
        getter = getattr(broker, "get_min_order_notional", None)
        if not callable(getter):
            return 0.0
        try:
            return float(getter(symbol) or 0.0)  # type: ignore
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not fetch minimum order notional for {symbol}: {e}")
            return 0.0

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

    def strategy_expectancy(self, strategy_name: str) -> float:
        """Calculate the average PnL (as fraction) for a given strategy."""
        if not strategy_name or strategy_name not in self.strategy_performance:
            return 0.0
        returns = self.strategy_performance[strategy_name]
        if not returns:
            return 0.0
        return float(np.mean(returns))

    # ------------------------------------------------------------------
    # Per‑broker trade execution
    # ------------------------------------------------------------------
    def _place_trade(
        self,
        broker,
        pm,
        symbol: str,
        action: str,
        quantity: float,
        last_price: float,
        stop_loss: float,
        atr: float,
        vol_stop_mult: float,
        strategy_name: str | None = None,
    ) -> bool:
        if self._earnings_nearby(symbol):
            self._alert_if_critical(
                f"Trade skipped for {symbol}: earnings nearby.",
                source=self._get_broker_source(broker),
                severity="warning"
            )
            log.warning(f"Earnings nearby for {symbol}, trade blocked.")
            return False

        if action == "SELL_SHORT" and not self._check_shortable(
            broker, symbol, quantity
        ):
            self._alert_if_critical(
                f"Short sale rejected for {symbol}: not enough shares",
                source=self._get_broker_source(broker),
                severity="warning"
            )
            return False

        slipped_price = self._apply_slippage(last_price, action)

        # ---------- ENTRY ----------
        if action in ("BUY", "SELL_SHORT"):
            broker_label = self._get_broker_source(broker)
            filled_qty = self._simulate_partial_fill(quantity)
            if filled_qty <= 0:
                return False

            min_notional = self._get_min_order_notional(broker, symbol)
            order_notional = filled_qty * slipped_price

            # Base-size check (Kucoin enforces both base qty AND notional)
            constraints_fn = getattr(broker, "get_min_order_constraints", None)
            if callable(constraints_fn):
                try:
                    min_base, _ = constraints_fn(symbol) # type: ignore
                    if min_base > 0 and filled_qty < min_base:
                        log.warning(
                            f"Skipping entry for {symbol}: quantity {filled_qty} "
                            f"is below exchange minimum base size {min_base}."
                        )
                        return False
                except Exception:  # noqa: BLE001, S110
                    pass

            if min_notional > 0 and order_notional < min_notional:
                log.warning(
                    f"Skipping entry for {symbol}: notional {order_notional:.8f} "
                    f"is below broker minimum {min_notional:.8f}."
                )
                return False

            use_bracket = getattr(broker, "supports_bracket", True)
            slipped_stop_loss = self._apply_slippage(
                stop_loss, "SELL" if action == "BUY" else "BUY_TO_COVER"
            )
            tp_price = slipped_price + (atr * vol_stop_mult * 2) if action == "BUY" else slipped_price - (atr * vol_stop_mult * 2)

            if use_bracket:
                try:
                    broker.connect()
                    if action == "BUY":
                        order_id, stop_id = broker.place_bracket_long(
                            symbol,
                            filled_qty,
                            slipped_price,
                            slipped_stop_loss,
                            tp_price,
                        )
                    else:
                        order_id, stop_id = broker.place_bracket_short(
                            symbol,
                            filled_qty,
                            slipped_price,
                            slipped_stop_loss,
                            tp_price,
                        )
                    if not order_id:
                        log.error(f"Failed to place bracket order for {symbol}")
                        self._alert_if_critical(
                            f"Trade failed for {symbol}: bracket order rejected",
                            source=self._get_broker_source(broker),
                            severity="important"
                        )
                        return False

                    fill = broker.wait_for_fill(order_id)
                    if fill["status"] != "Filled" or fill["filled"] == 0:
                        log.error(f"Order not filled for {symbol}: {fill['status']}")
                        self._alert_if_critical(
                            f"Trade failed for {symbol}: order not filled",
                            source=self._get_broker_source(broker)
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
                    broker_label = self._get_broker_source(broker)

                    pm.open_position(
                        Position(
                            symbol=symbol,
                            side="BUY" if action == "BUY" else "SELL",
                            quantity=filled_qty,
                            entry_price=net_entry_price,
                            stop_loss=slipped_stop_loss,
                            stop_order_id=safe_stop_id,
                            entry_time=datetime.now(timezone.utc),
                            strategy=strategy_name,
                        )
                    )
                    self.email.send_trade_alert(symbol, action, filled_qty, avg_price, source=broker_label)
                    direction_emoji = "🟢" if action == "BUY" else "🔴"
                    direction_text = "BUY" if action == "BUY" else "SELL SHORT"
                    strategy_display = strategy_name if strategy_name is not None else "Unknown"

                    entry_message = (
                        f"<b>{direction_emoji} ENTRY — {symbol}</b>\n"
                        f"Strategy: {strategy_display}\n"
                        f"Exchange: <code>{broker_label}</code>\n"
                        f"Quantity: <code>{filled_qty}</code>\n"
                        f"Entry: <code>${avg_price:,.4f}</code>\n"
                        f"Stop: <code>${slipped_stop_loss:,.4f}</code>\n"
                        f"Target: <code>${tp_price:,.4f}</code>\n"
                        f"Risk: {self.kelly_fraction()*100:.1f}% of portfolio\n"
                        f"<i>Not financial advice.</i>"
                    )
                    self.telegram.send_channel_signal(entry_message)
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
                    self._alert_if_critical(
                        f"Trade failed for {symbol}: {e}",
                        source=self._get_broker_source(broker),
                        severity="important"
                    )
                    return False

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
                    if not order_result or not order_result.get("order_id"):
                        log.error(f"Plain order failed for {symbol}: no order ID")
                        self._alert_if_critical(
                            f"Trade failed for {symbol}: plain order rejected",
                            source=self._get_broker_source(broker),
                            severity="important"
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
                    broker_label = self._get_broker_source(broker)

                    pm.open_position(
                        Position(
                            symbol=symbol,
                            side="BUY" if action == "BUY" else "SELL",
                            quantity=filled_qty,
                            entry_price=net_entry_price,
                            stop_loss=stop_loss,
                            stop_order_id=0,
                            entry_time=datetime.now(timezone.utc),
                            strategy=strategy_name,
                        )
                    )
                    self.email.send_trade_alert(symbol, action, filled_qty, avg_price, source=broker_label)
                    # Add telegram channel post after successful entry
                    direction = "🟢 BUY" if action == "BUY" else "🔴 SELL SHORT"
                    self.telegram.send_channel_signal(
                        f"<b>{direction} — {symbol}</b>\n"
                        f"Exchange: <code>{broker_label}</code>\n"
                        f"Quantity: <code>{filled_qty}</code>\n"
                        f"Entry: <code>${avg_price:,.4f}</code>\n"
                        f"Stop: <code>${stop_loss:,.4f}</code>\n"
                        f"<i>Not financial advice.</i>"
                    )
                    log.success(
                        f"Filled {action} {filled_qty} {symbol} @ ${avg_price:.2f} (plain)"
                    )
                    return True

                except Exception as e:  # noqa: BLE001
                    log.exception(f"Entry error for {symbol}: {e}")
                    self._alert_if_critical(
                        f"Trade failed for {symbol}: {e}",
                        source=self._get_broker_source(broker),
                        severity="important"
                    )
                    return False

        # ---------- EXIT ----------
        else:
            broker_label = self._get_broker_source(broker)
            pos = pm.positions.get(symbol)
            if not pos:
                log.warning(f"No internal position for {symbol}")
                self._alert_if_critical(
                    f"Trade failed for {symbol}: no position to close",
                    source=self._get_broker_source(broker),
                    severity="important"
                )
                return False

            filled_qty = self._simulate_partial_fill(quantity)
            filled_qty = min(filled_qty, pos.quantity)
            min_notional = self._get_min_order_notional(broker, symbol)
            order_notional = filled_qty * last_price

            # Base-size check (Kucoin enforces both base qty AND notional).
            # Only skip when the position is genuinely too small to trade.
            constraints_fn = getattr(broker, "get_min_order_constraints", None)
            if callable(constraints_fn):
                try:
                    min_base, _ = constraints_fn(symbol)  # type: ignore
                except Exception:  # noqa: BLE001
                    min_base = 0.0

                if min_base > 0 and filled_qty < min_base:
                    # Genuine dust — remove from internal tracking and move on.
                    if symbol not in self._dust_warned:
                        log.warning(
                            f"Skipping exit for {symbol}: quantity {filled_qty} "
                            f"is below exchange minimum base size {min_base}; "
                            f"removed dust position."
                        )
                        self._dust_warned.add(symbol)
                    else:
                        log.debug(
                            f"Skipping exit for {symbol}: dust ({filled_qty} < {min_base})"
                        )

                    # Compute P&L for the trade log
                    if pos.entry_price <= 0:
                        pnl_dollar = 0.0
                    elif pos.side == "BUY":
                        pnl_dollar = (last_price - pos.entry_price) * pos.quantity
                    else:
                        pnl_dollar = (pos.entry_price - last_price) * pos.quantity

                    self._log_trade(
                        symbol,
                        action,
                        pos.quantity,
                        pos.entry_price,
                        last_price,
                        pnl_dollar,
                        pos.side,
                        strategy_name,
                    )
                    pm.close_position(symbol)
                    return False

            if min_notional > 0 and order_notional < min_notional:
                # Calculate PnL for the dust position being removed
                if pos.entry_price <= 0:
                    pnl_dollar = 0.0
                else:
                    if pos.side == "BUY":
                        pnl_dollar = (last_price - pos.entry_price) * pos.quantity
                    else:  # SELL
                        pnl_dollar = (pos.entry_price - last_price) * pos.quantity
                # Log the trade
                self._log_trade(
                    symbol,
                    action,
                    pos.quantity,
                    pos.entry_price,
                    last_price,
                    pnl_dollar,
                    pos.side,
                    strategy_name,
                )
                # Remove the position internally
                pm.close_position(symbol)
                log.warning(
                    f"Skipping exit for {symbol}: notional {order_notional:.8f} "
                    f"is below broker minimum {min_notional:.8f}; removed dust position."
                )
                return False

            try:
                broker.connect()
                order_result = broker.place_order(
                    symbol=symbol, side=action, quantity=filled_qty, order_type="MKT"
                )
                if not order_result:
                    log.error(f"Failed to place closing order for {symbol}")
                    self._alert_if_critical(
                        f"Trade failed for {symbol}: closing order rejected",
                        source=self._get_broker_source(broker),
                        severity="critical"
                    )
                    return False

                fill = broker.wait_for_fill(order_result["order_id"])
                if fill["status"] != "Filled" or fill["filled"] == 0:
                    log.error(
                        f"Closing order not filled for {symbol}: {fill['status']}"
                    )
                    self._alert_if_critical(
                        f"Trade failed for {symbol}: closing order not filled",
                        source=self._get_broker_source(broker),
                        severity="critical"
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

                if pos.entry_price <= 0:
                    log.warning(f"Unknown entry price for {symbol}; treating close as no P&L.")
                    pnl_frac = 0.0
                    pnl_dollar = 0.0
                else:
                    pnl_frac = (
                        (net_close_price - pos.entry_price) / pos.entry_price
                        if pos.side == "BUY"
                        else (pos.entry_price - net_close_price) / pos.entry_price
                    )
                    pnl_dollar = (
                        (net_close_price - pos.entry_price) * filled_qty
                        if pos.side == "BUY"
                        else (pos.entry_price - net_close_price) * filled_qty
                    )

                self.trade_results.append(("win" if pnl_frac > 0 else "loss", pnl_frac))

                self._log_trade(
                    symbol,
                    action,
                    filled_qty,
                    pos.entry_price,
                    net_close_price,
                    pnl_dollar,
                    pos.side,
                    strategy_name,
                )

                if filled_qty >= pos.quantity:
                    pm.close_position(symbol)
                else:
                    pos.quantity -= filled_qty

                # ── Cooldown on loss ──
                if pnl_frac < 0:
                    cooldown_minutes = self.config.get("rotation", {}).get(
                        "cooldown_minutes", 240
                    )
                    cooldown_until = datetime.now(timezone.utc) + timedelta(
                        minutes=cooldown_minutes
                    )
                    self.symbol_cooldowns[symbol] = cooldown_until
                    log.info(
                        f"Set cooldown for {symbol} until {cooldown_until.isoformat()} "
                        f"after losing trade ({pnl_frac:.2%})."
                    )
                    broker_label = self._get_broker_source(broker)

                self.email.send_trade_alert(symbol, action, filled_qty, avg_price)
                if action == "SELL":
                    direction_emoji = "🔵"
                    direction_text = "CLOSED"
                else:  # BUY_TO_COVER
                    direction_emoji = "🟣"
                    direction_text = "COVERED"

                holding_time = datetime.now(timezone.utc) - pos.entry_time
                total_seconds = holding_time.total_seconds()
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                pnl_amount = pnl_dollar
                strategy_display = strategy_name if strategy_name else "EXIT"
                # Format P&L with more precision for small values
                if abs(pnl_amount) < 0.01:
                    pnl_amount_str = f"{pnl_amount:+.4f}"
                else:
                    pnl_amount_str = f"{pnl_amount:+.2f}"
                if abs(pnl_frac) < 0.0001:
                    pnl_percent_str = f"{pnl_frac:+.6%}"
                else:
                    pnl_percent_str = f"{pnl_frac:+.2%}"

                exit_message = (
                    f"<b>{direction_emoji} {direction_text} — {symbol}</b>\n"
                    f"Strategy: {strategy_display}\n"
                    f"Entry: <code>{pos.entry_price:,.4f}</code> → Exit: <code>${avg_price:,.4f}</code>\n"
                    f"Result: {pnl_percent_str}  |  P&L: <code>${pnl_amount_str}</code>\n"
                    f"Held: {hours}h {minutes}m\n"
                    f"<i>Not financial advice.</i>"
                )
                self.telegram.send_channel_signal(exit_message)
                log.success(
                    f"Closed {action} {filled_qty} {symbol} @ ${avg_price:.2f}, P&L {pnl_frac:.4%}"
                )
                return True

            except Exception as e:  # noqa: BLE001
                log.exception(f"Exit execution error for {symbol}: {e}")
                self._alert_if_critical(
                    f"Trade failed for {symbol}: {e}",
                    source=self._get_broker_source(broker),
                    severity="critical"
                )
                return False

        return False

    # ------------------------------------------------------------------
    # De-risking & rotation
    # ------------------------------------------------------------------
    def _enforce_risk_limits(self, broker, pm, rm, latest_prices: dict, capital: float):
        """Reduce positions that breach risk limits.

        For crypto brokers with a minimum base trade size, a partial reduction
        that would leave dust is converted into a full close instead. This
        prevents the hourly loop of "reduce 0.01 → below minimum → skip"
        that otherwise repeats indefinitely.

        Market-hours guard: IBKR rejects or queues orders after NYSE close,
        which causes a pile-up of stale de-risk orders that get mass-cancelled
        at the next open (Error 202). For equity brokers, skip de-risk entirely
        when the US session is closed.

        Stale-order guard: before each de-risk order, cancel any pending orders
        for the same symbol so duplicates never accumulate.
        """
        # --- Market hours guard (IBKR rejects/queues after-hours orders) ---
        if not self._is_market_open(broker):
            log.debug(
                f"Market closed for {broker.__class__.__name__}; "
                f"skipping de-risk this iteration."
            )
            return

        max_single = capital * rm.config.get(
            "max_position_pct",
            self.config["risk_management"]["max_position_pct"],
        )
        max_gross = capital * rm.config.get(
            "max_gross_exposure",
            self.config["risk_management"]["max_gross_exposure"],
        )
        max_net = capital * rm.config.get(
            "max_net_exposure",
            self.config["risk_management"]["max_net_exposure"],
        )

        def _min_base(sym: str) -> float:
            """Best-effort lookup of the broker's minimum base trade size."""
            constraints_fn = getattr(broker, "get_min_order_constraints", None)
            if not callable(constraints_fn):
                return 0.0
            try:
                min_base, _ = constraints_fn(sym)  # type: ignore
                return float(min_base or 0.0)
            except Exception:  # noqa: BLE001
                return 0.0

        def _adjust_reduce_qty(sym: str, pos, requested: float) -> float:
            """Return the quantity to reduce, upgrading to a full close when
            a partial reduction would leave the position below min base size."""
            qty = requested

            # Stocks: whole shares only
            if not self._is_crypto(sym):
                return float(int(qty)) if qty >= 1 else 0.0

            # Crypto: respect minimum base size on both sides of the trade
            min_base = _min_base(sym)
            if min_base <= 0:
                return qty

            remaining = pos.quantity - qty

            # Case 1: the reduction itself is below the minimum → close fully
            if qty < min_base:
                log.warning(
                    f"De-risk {sym}: reduction {qty:.8f} is below broker minimum "
                    f"{min_base:.8f}; closing entire position instead."
                )
                return pos.quantity

            # Case 2: the reduction is fine, but remaining would be dust → close fully
            if remaining < min_base:
                log.warning(
                    f"De-risk {sym}: partial reduce would leave {remaining:.8f} "
                    f"below minimum {min_base:.8f}; closing entire position instead."
                )
                return pos.quantity

            return qty

        def _de_risk(sym: str, pos, reduce_qty: float, price: float) -> None:
            """Cancel stale orders for the symbol, then place one clean de-risk."""
            cancelled = self._cancel_pending_orders_for(broker, sym)
            if cancelled:
                log.info(
                    f"De-risk {sym}: cancelled {cancelled} stale order(s) "
                    f"before placing new de-risk."
                )
            side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
            self._place_trade(
                broker, pm, sym, side, reduce_qty, price, 0.0, 0.0, 0.0,
                strategy_name="De-risk",
            )

        # --------------------------------------------------------------
        # Single-name concentration
        # --------------------------------------------------------------
        for sym, pos in list(pm.positions.items()):
            price = latest_prices.get(sym, pos.entry_price)
            if price <= 0:
                continue
            notional = pos.quantity * price
            if notional <= max_single + 1e-6:
                continue

            excess = notional - max_single
            reduce_qty = _adjust_reduce_qty(sym, pos, excess / price)
            if reduce_qty <= 0:
                log.debug(f"De-risk {sym}: no valid reduction quantity; skipping.")
                continue

            log.warning(
                f"De-risking {sym}: reducing {reduce_qty:.8f} to enforce "
                f"single-name limit."
            )
            _de_risk(sym, pos, reduce_qty, price)

        # --------------------------------------------------------------
        # Gross exposure
        # --------------------------------------------------------------
        current_gross = rm.get_gross_exposure(latest_prices)
        if current_gross > max_gross + 1e-6:
            overage = current_gross - max_gross
            positions_sorted = sorted(
                pm.positions.items(),
                key=lambda kv: kv[1].quantity
                * latest_prices.get(kv[0], kv[1].entry_price),
                reverse=True,
            )
            for sym, pos in positions_sorted:
                if overage <= 0:
                    break
                price = latest_prices.get(sym, pos.entry_price)
                if price <= 0:
                    continue
                notional = pos.quantity * price
                reduce_notional = min(notional, overage)
                raw_qty = reduce_notional / price
                reduce_qty = _adjust_reduce_qty(sym, pos, raw_qty)
                if reduce_qty <= 0:
                    continue

                log.warning(
                    f"De-risking gross: reducing {sym} by {reduce_qty:.8f} "
                    f"to lower total exposure."
                )
                _de_risk(sym, pos, reduce_qty, price)
                overage -= reduce_qty * price

        # --------------------------------------------------------------
        # Net exposure
        # --------------------------------------------------------------
        current_net = rm.get_net_exposure(latest_prices)
        if abs(current_net) > max_net + 1e-6:
            overage_net = abs(current_net) - max_net
            target_side = "BUY" if current_net > 0 else "SELL"
            positions_sorted = sorted(
                pm.positions.items(),
                key=lambda kv: kv[1].quantity
                * latest_prices.get(kv[0], kv[1].entry_price),
                reverse=True,
            )
            for sym, pos in positions_sorted:
                if overage_net <= 0:
                    break
                if pos.side != target_side:
                    continue
                price = latest_prices.get(sym, pos.entry_price)
                if price <= 0:
                    continue
                notional = pos.quantity * price
                reduce_notional = min(notional, overage_net)
                raw_qty = reduce_notional / price
                reduce_qty = _adjust_reduce_qty(sym, pos, raw_qty)
                if reduce_qty <= 0:
                    continue

                log.warning(
                    f"De-risking net: reducing {sym} by {reduce_qty:.8f} "
                    f"to lower net exposure."
                )
                _de_risk(sym, pos, reduce_qty, price)
                overage_net -= reduce_qty * price

    def _is_market_open(self, broker) -> bool:
        """Return True if the primary market for this broker is currently open.

        Crypto and synthetics trade 24/7. US equities (IBKR) are only open
        Mon–Fri 09:30–16:00 ET. Attempting to place stock orders outside
        these hours causes IBKR to queue them, which piles up and gets
        mass-cancelled at the next open with Error 202.
        """
        if broker.__class__.__name__ != "IBBroker":
            return True  # crypto, synthetics, and other brokers trade 24/7

        now_utc = datetime.now(timezone.utc)

        # Determine whether US markets are in DST by checking the 2nd Sunday
        # in March at 07:00 UTC and the 1st Sunday in November at 06:00 UTC.
        year = now_utc.year
        march = datetime(year, 3, 1, 7, 0, tzinfo=timezone.utc)
        dst_start = march + timedelta(days=(6 - march.weekday() + 7) % 7 + 7)
        november = datetime(year, 11, 1, 6, 0, tzinfo=timezone.utc)
        dst_end = november + timedelta(days=(6 - november.weekday()) % 7)

        is_dst = dst_start <= now_utc < dst_end
        et_offset_hours = -4 if is_dst else -5
        now_et = now_utc + timedelta(hours=et_offset_hours)

        if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False

        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_et <= now_et <= close_et

    def _cancel_pending_orders_for(self, broker, symbol: str) -> int:
        """Cancel all open orders for a symbol. Returns number cancelled.

        Safe no-op for brokers that don't expose cancel_all_for_symbol.
        """
        cancel_fn = getattr(broker, "cancel_all_for_symbol", None)
        if not callable(cancel_fn):
            return 0
        try:
            return int(cancel_fn(symbol) or 0) # type: ignore # type: ignore
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not cancel pending orders for {symbol}: {e}")
            return 0

    def _rotate_underperformers(
        self, broker, pm, latest_prices: dict, active_symbols: list[str]
    ):
        """Close losing positions that are no longer in the active symbol list."""
        active_set = set(active_symbols)
        for sym, pos in list(pm.positions.items()):
            if sym in active_set:
                continue
            price = latest_prices.get(sym, pos.entry_price)
            if price <= 0 or pos.entry_price <= 0:
                continue

            if pos.side == "BUY":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price

            if pnl_pct < 0:
                side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                log.warning(
                    f"Rotating out underperforming {sym}: P&L {pnl_pct:.2%}. Closing position."
                )
                self._place_trade(broker, pm, sym, side, pos.quantity, price, 0.0, 0.0, 0.0, strategy_name="Underperformer")

    def _apply_dynamic_exits(
        self, broker, pm, latest_prices: dict
    ):
        """Close positions based on time held and unrealized loss."""
        rotation_cfg = self.config.get("rotation", {})
        max_holding_hours = rotation_cfg.get("max_holding_hours", 48)
        max_unrealized_loss_pct = rotation_cfg.get("max_unrealized_loss_pct", -0.05)

        now = datetime.now(timezone.utc)
        for sym, pos in list(pm.positions.items()):
            price = latest_prices.get(sym, pos.entry_price)
            if price <= 0 or pos.entry_price <= 0:
                continue

            # Time-based exit
            if pos.entry_time is not None:
                holding_hours = (now - pos.entry_time).total_seconds() / 3600.0
                if holding_hours > max_holding_hours:
                    side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                    log.warning(
                        f"Time stop triggered for {sym}: held {holding_hours:.1f}h > {max_holding_hours}h."
                    )
                    self._place_trade(broker, pm, sym, side, pos.quantity, price, 0.0, 0.0, 0.0, strategy_name="Time Stop")
                    continue

            # Unrealized loss exit
            if pos.side == "BUY":
                pnl_pct = (price - pos.entry_price) / pos.entry_price
            else:
                pnl_pct = (pos.entry_price - price) / pos.entry_price

            if pnl_pct < max_unrealized_loss_pct:
                side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                log.warning(
                    f"Drawdown stop triggered for {sym}: P&L {pnl_pct:.2%} < {max_unrealized_loss_pct:.2%}."
                )
                self._place_trade(broker, pm, sym, side, pos.quantity, price, 0.0, 0.0, 0.0, strategy_name="Drawdown Stop")

    # ------------------------------------------------------------------
    # Main iteration loop – runs over all brokers
    # ------------------------------------------------------------------
    def run_iteration(self):
        log.info("--- Running iteration ---")
        self._crypto_fail_streak = 0
        combined_nav = 0.0
        all_latest_prices = {}

        for broker_name, broker in self.broker_manager.iterate_all():
            if broker_name not in self.risk_managers:
                log.info(f"Skipping '{broker_name}' – not available.")
                continue

            rm = self.risk_managers[broker_name]
            pm = self.position_managers[broker_name]
            last_logged_qty = self.broker_last_logged_qty.setdefault(broker_name, {})

            account: dict[str, object] | None = None
            last_error: Exception | None = None
            for attempt in range(2):  # one retry
                try:
                    account = broker.get_account_info()
                    break
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    if attempt == 0:
                        log.warning(
                            f"Retrying '{broker_name}' account fetch after error: {e}"
                        )
                        time.sleep(5)

            if account is None:
                log.warning(
                    f"Could not reach '{broker_name}' after retries: {last_error}. Skipping iteration."
                )
                continue

            capital = float(account["net_liquidation"])  # pyright: ignore[reportArgumentType] # type: ignore

            # Guard: IBKR occasionally reports 0 (or NaN) for NetLiquidation during
            # reconnections or when accountValues() returns an empty list. Treating
            # that as a real capital drop would halt trading and corrupt daily P&L.
            if not np.isfinite(capital) or capital <= 0:
                log.warning(
                    f"Broker '{broker_name}' reported invalid capital "
                    f"({capital}); skipping portfolio update this iteration."
                )
                self._alert_if_critical(
                    f"Invalid capital guard trip: {broker_name} reported capital {capital}",
                    source="capital_guard",
                    severity="important"
                )
                continue

            rm.update_portfolio(capital - rm.current_capital, 0)
            if not rm.can_trade():
                log.warning(
                    f"Trading halted for {broker_name}. Daily P&L: {rm.daily_pnl}, Capital: {rm.current_capital}, Peak: {rm.peak_capital}"
                )
                continue

            # Sync positions for all brokers (IBKR and KuCoin)
            self._sync_positions_from_broker(broker, pm)

            for sym, pos in pm.positions.items():
                last_logged_qty[sym] = pos.quantity

            latest_prices = {}

            for sym, pos in list(pm.positions.items()):
                if self._is_crypto(sym):
                    df = self._get_bars(sym, count=200)
                else:
                    df = self._get_bars(sym, count=200)
                if df.empty:
                    continue
                last_price = df["close"].iloc[-1]
                latest_prices[sym] = last_price

                # Update trailing stop
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

                # Check stop-loss trigger
                if (pos.side == "BUY" and last_price <= pos.stop_loss) or (
                    pos.side == "SELL" and last_price >= pos.stop_loss
                ):
                    log.warning(
                        f"Stop-loss triggered for {sym} ({broker_name}) at {last_price:.2f}. Attempting to close."
                    )
                    if not pos.stop_order_id:
                        success = self._place_trade(
                            broker,
                            pm,
                            sym,
                            "SELL" if pos.side == "BUY" else "BUY_TO_COVER",
                            pos.quantity,
                            last_price,
                            stop_loss=0.0,
                            atr=0.0,
                            vol_stop_mult=0.0,
                            strategy_name="Stop Loss",
                        )
                        if success:
                            log.success(f"Stop-loss closure executed for {sym}.")
                        else:
                            log.error(f"Failed to close {sym} on stop-loss.")
                            self._alert_if_critical(
                                f"Stop-loss closure failed for {sym}",
                                source="stop_loss",
                                severity="important"
                            )
                    else:
                        log.warning(
                            f"Broker stop order {pos.stop_order_id} exists for {sym}; skipping manual close."
                        )

            self._reconcile_and_log_closed_positions(pm, last_logged_qty, latest_prices)
            self.broker_latest_prices[broker_name] = latest_prices
            rm.recalc_open_risk(latest_prices)

            # ── Enforce hard risk limits ──
            self._enforce_risk_limits(broker, pm, rm, latest_prices, capital)

            # ── Rotation & dynamic exits ──
            if self.config.get("rotation", {}).get("enabled", False):
                self._rotate_underperformers(
                    broker, pm, latest_prices, self.symbols_by_broker[broker_name]
                )
                self._apply_dynamic_exits(broker, pm, latest_prices)

            # ──────────── Signal generation (per‑broker strategies) ────────────
            symbols = list(
                set(self.symbols_by_broker[broker_name]) | set(pm.positions.keys())
            )
            broker_strategies = self.strategies_by_broker.get(broker_name, self.strategies)

            for symbol in symbols:
                pos = pm.positions.get(symbol)

                # ── FALLBACK: If internal position missing, ask broker directly ──
                if pos is None and not self._is_crypto(symbol):
                    try:
                        broker_positions = broker.get_positions()
                        for bp in broker_positions:
                            if bp.get("symbol") == symbol:
                                qty = bp.get("quantity", 0.0)
                                avg_cost = bp.get("avg_cost", 0.0)
                                if qty != 0:
                                    side = "BUY" if qty > 0 else "SELL"
                                    init_stop = float('inf') if side == "SELL" else 0.0
                                    pm.open_position(
                                        Position(
                                            symbol=symbol,
                                            side=side,
                                            quantity=abs(qty),
                                            entry_price=avg_cost,
                                            stop_loss=init_stop,
                                            stop_order_id=0,
                                            entry_time=datetime.now(timezone.utc),
                                            strategy=None,
                                        )
                                    )
                                    pos = pm.positions[symbol]
                                    log.warning(
                                        f"Fallback sync: found broker position for {symbol} "
                                        f"({side} {abs(qty)} shares). Internal manager updated."
                                    )
                                break
                    except Exception as e:  # noqa: BLE001
                        log.error(f"Fallback broker position check failed for {symbol}: {e}")

                current_side = pos.side if pos else None

                # ── Cooldown check for new entries ──
                if pos is None and symbol in self.symbol_cooldowns:
                    cooldown_until = self.symbol_cooldowns[symbol]
                    if datetime.now(timezone.utc) < cooldown_until:
                        log.info(
                            f"Skipping {symbol}: in cooldown until {cooldown_until.isoformat()}."
                        )
                        continue

                if self._is_crypto(symbol):
                    df = self._get_bars(symbol, count=200)
                else:
                    df = self._get_bars(symbol, count=200)
                if df.empty:
                    continue
                last_price = df["close"].iloc[-1]
                latest_prices[symbol] = last_price

                signals_to_resolve = []
                for strategy in broker_strategies:
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
                else:
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
                        # Spot-only brokers cannot short. Skip the signal entirely.
                        if not getattr(broker, "supports_shorting", True):
                            log.info(
                                f"{symbol}: ENTER_SHORT ignored – "
                                f"{broker.__class__.__name__} does not support shorting."
                            )
                            continue
                        action = "SELL_SHORT"

                if action is None:
                    continue

                reasons = [s.name for s in signals_set]
                log.info(
                    f"Resolved {symbol} ({broker_name}): {reasons} -> {action} (current side={current_side})"
                )

                # Filter reasons to only include relevant signals for strategy display
                if action in ("BUY", "SELL_SHORT"):
                    # Entry actions: show ENTER signals
                    strategy_reasons = [r for r in reasons if r.startswith("ENTER_")]
                elif action in ("SELL", "BUY_TO_COVER"):
                    # Exit actions: show EXIT signals
                    strategy_reasons = [r for r in reasons if r.startswith("EXIT_")]
                else:
                    # Fallback: use all reasons
                    strategy_reasons = reasons

                if action in ("SELL", "BUY_TO_COVER"):
                    if not pos:
                        log.warning(
                            f"Exit signal for {symbol} but no position – skipping."
                        )
                        continue
                    quantity = pos.quantity
                    strategy_display = strategy_reasons[0] if strategy_reasons else "Signal"
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
                        strategy_name=strategy_display,
                    )
                    if success:
                        log.success(
                            f"Exit executed: {action} {quantity} {symbol} on {broker_name}"
                        )
                        self.telegram.send_trade_alert(
                            symbol, action, quantity, last_price  # type: ignore[arg-type]
                        )
                        self.discord.send_trade_alert(
                            symbol, action, quantity, last_price  # type: ignore[arg-type]
                        )
                    continue

                atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
                vol_stop_mult = rm.config.get(
                    "volatility_stop_multiplier",
                    self.config["risk_management"]["volatility_stop_multiplier"],
                )

                if action == "BUY":
                    stop_loss = last_price - (atr * vol_stop_mult)
                elif action == "SELL_SHORT":
                    stop_loss = last_price + (atr * vol_stop_mult)
                else:
                    continue

                quantity = broker_strategies[0].calculate_position_size(
                    capital=capital,
                    risk_per_trade=self.kelly_fraction(),
                    entry_price=last_price,
                    stop_loss_price=stop_loss,
                )
                if quantity == 0:
                    continue

                # ── Auto‑scale quantity to respect limits ──
                max_single = capital * rm.config.get(
                    "max_position_pct",
                    self.config["risk_management"]["max_position_pct"],
                )
                max_gross = capital * rm.config.get(
                    "max_gross_exposure",
                    self.config["risk_management"]["max_gross_exposure"],
                )

                current_gross = rm.get_gross_exposure(latest_prices)
                gross_budget = max_gross - current_gross
                single_budget = max_single - rm.get_position_notional(symbol, last_price)
                notional_budget = min(gross_budget, single_budget)
                notional_budget = max(0.0, notional_budget)

                proposed_notional = quantity * last_price
                if proposed_notional > notional_budget:
                    if self._is_crypto(symbol):
                        scaled_qty = notional_budget / last_price if last_price > 0 else 0.0
                    else:
                        scaled_qty = int(notional_budget / last_price) if last_price > 0 else 0
                    if scaled_qty < quantity:
                        log.info(
                            f"Scaling {symbol} quantity {quantity} → {scaled_qty} to fit limits "
                            f"(proposed={proposed_notional:.2f}, budget={notional_budget:.2f})"
                        )
                        quantity = scaled_qty

                if quantity == 0:
                    log.warning(f"{symbol} quantity scaled to 0 — skipping trade.")
                    continue

                order_valid, msg = rm.validate_order(
                    symbol, action, quantity, last_price, stop_loss
                )
                if not order_valid:
                    log.warning(f"Order rejected: {msg}")
                    continue

                # ── Hard single-name concentration check ──
                existing_pos = pm.positions.get(symbol)
                existing_notional = existing_pos.quantity * last_price if existing_pos else 0.0
                new_notional = existing_notional + (quantity * last_price)
                if new_notional > max_single + 1e-6:
                    log.warning(
                        f"Single-name limit for {symbol}: existing {existing_notional:.2f}, "
                        f"proposed {quantity * last_price:.2f}, total {new_notional:.2f} > {max_single:.2f}"
                    )
                    continue

                # ── Hard gross exposure check ──
                current_gross = rm.get_gross_exposure(latest_prices)
                if current_gross + (quantity * last_price) > max_gross + 1e-6:
                    log.warning(
                        f"Gross exposure limit would be breached for {symbol}: "
                        f"current {current_gross:.2f}, proposed {quantity * last_price:.2f}, "
                        f"limit {max_gross:.2f}"
                    )
                    continue

                if not self._check_net_exposure(
                    rm, action, quantity, last_price, latest_prices
                ):
                    log.warning(f"Net exposure limit for {symbol}")
                    continue

                if self._earnings_nearby(symbol):
                    log.warning(f"Earnings nearby for {symbol}, skipping.")
                    continue

                strategy_display = reasons[0] if reasons else "Signal"
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
                    strategy_name=strategy_display,
                )
                if success:
                    log.success(
                        f"LIVE PAPER ORDER: {action} {quantity} {symbol} on {broker_name}"
                    )
                    self.telegram.send_trade_alert(
                        symbol, action, quantity, last_price  # type: ignore[arg-type]
                    )
                    self.discord.send_trade_alert(
                        symbol, action, quantity, last_price  # type: ignore[arg-type]
                    )

            combined_nav += capital

        self.equity_history.append((datetime.now(timezone.utc), combined_nav))
        self.latest_prices = all_latest_prices

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _sync_positions_from_broker(self, broker, pm):
        """Synchronise internal positions with broker positions for any broker."""
        try:
            broker_positions = broker.get_positions()
            log.debug(
                f"Position sync: broker '{broker.__class__.__name__}' returned "
                f"{len(broker_positions)} positions: "
                f"{[(p.get('symbol'), p.get('quantity')) for p in broker_positions]}"
            )

            symbols_in_broker = {p["symbol"] for p in broker_positions}
            for sym in list(pm.positions.keys()):
                if sym not in symbols_in_broker:
                    pm.close_position(sym)
                    log.warning(f"Removed stale position {sym}")

            for broker_pos in broker_positions:
                sym = broker_pos["symbol"]
                qty = broker_pos["quantity"]
                avg_cost = broker_pos.get("avg_cost", 0.0)

                if qty == 0:
                    if sym in pm.positions:
                        pm.close_position(sym)
                    continue

                side = "BUY" if qty > 0 else "SELL"
                init_stop = float("inf") if side == "SELL" else 0.0

                if sym not in pm.positions:
                    # Use broker avg_cost if valid, otherwise fall back to latest price
                    entry_price = avg_cost if avg_cost > 0.0 else self.latest_prices.get(sym, 0.0)
                    entry_time = datetime.now(timezone.utc)

                    # If we have a KucoinBroker and the avg_cost is zero or invalid, try to get the average cost from trade history
                    if avg_cost <= 0.0 and hasattr(broker, 'get_average_cost') and self._is_crypto(sym):
                        try:
                            avg_cost_from_trades, last_trade_time = broker.get_average_cost(sym)
                            if avg_cost_from_trades > 0.0:
                                entry_price = avg_cost_from_trades
                                entry_time = last_trade_time
                        except Exception as e:  # noqa: BLE001
                            log.debug(f"Failed to get average cost from trade history for {sym}: {e}")

                    pm.open_position(
                        Position(
                            symbol=sym,
                            side=side,
                            quantity=abs(qty),
                            entry_price=entry_price,
                            stop_loss=init_stop,
                            stop_order_id=0,
                            entry_time=entry_time,
                            strategy=None,
                        )
                    )
                else:
                    pos = pm.positions[sym]
                    pos.quantity = abs(qty)
                    if avg_cost > 0:
                        pos.entry_price = avg_cost
                    # keep existing entry_price if avg_cost is zero or invalid
                    if pos.stop_loss is None or (
                        pos.side == "SELL" and pos.stop_loss == 0.0
                    ):
                        pos.stop_loss = init_stop
            log.debug(
                f"Position manager state after sync for {broker.__class__.__name__}: "
                f"{[(sym, pos.side, pos.quantity) for sym, pos in pm.positions.items()]}"
            )
        except Exception as e:  # noqa: BLE001
            log.error(f"Position sync failed: {e}")
            self._alert_if_critical(
                f"Position sync failed: {e}",
                source="position_sync",
                severity="important"
            )

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
                sym, action, exit_qty, pos.entry_price, exit_price, pnl_dollar, pos.side, ""
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

        if self.scanner_enabled and self.scanner:
            scan_time = self.config["scanner"].get("time", "08:00")
            schedule.every().day.at(scan_time).do(self._run_stock_scanner)
            for broker_name in self.risk_managers:
                if self._is_crypto_broker(broker_name):
                    schedule.every().day.at(scan_time).do(
                        self._run_crypto_scanner_for_broker,
                        broker_name=broker_name,
                    )
            # schedule.every().day.at(scan_time).do(self._run_clone_monitor)  # Disabled clone monitor - vanity metric
            schedule.every().day.at(scan_time).do(self._run_trending_scanner)

        # Schedule EOD risk report and time-based caps
        schedule.every().day.at(self.eod_report_utc_time).do(self._log_eod_risk_report)
        schedule.every().day.at(self.overnight_cap_utc_time).do(self._apply_overnight_cap)
        schedule.every().day.at(self.weekend_flatten_utc_time).do(self._apply_weekend_flatten)

        # Schedule market data capture for blotter (every 15 minutes)
        schedule.every(15).minutes.do(self._capture_market_data)


        # Schedule African market reports and snapshots
        if self.african_enabled:
            african_cfg = self.config.get("african_scanner", {})
            schedule_times = african_cfg.get("schedule", {})
            # Daily snapshot after market close
            schedule.every().day.at(schedule_times.get("snapshot", "12:30")).do(self._capture_african_snapshots)
            # Morning report
            schedule.every().day.at(schedule_times.get("morning_report", "05:45")).do(self._run_african_morning_reports)
            # Close report
            schedule.every().day.at(schedule_times.get("close_report", "12:15")).do(self._run_african_close_report)

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

        # Start health endpoint server
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        health_port = 8001
        log.info(f"Health endpoint available at http://127.0.0.1:{health_port}/health")

        while self.is_running:
            schedule.run_pending()
            time.sleep(30)

        log.info("Trading bot stopped.")

    def _reset_daily_pnl(self):
        for rm in self.risk_managers.values():
            rm.reset_daily_pnl()

    def _log_eod_risk_report(self):
        """Log a per-broker summary of open positions and exposure at end of day."""
        log.info("=== End-of-day risk report ===")
        for broker_name, pm in self.position_managers.items():
            if not pm.positions:
                log.info(f"{broker_name}: no open positions")
                continue

            latest_prices = self.broker_latest_prices.get(broker_name, {})
            total_value = 0.0
            rows = []
            for sym, pos in pm.positions.items():
                price = latest_prices.get(sym, pos.entry_price)
                notional = pos.quantity * price if price > 0 else 0.0
                total_value += notional
                rows.append((sym, pos.side, pos.quantity, price, notional))

            # Compute concentration using the broker's current capital as denominator
            try:
                account = self.broker_manager.brokers[broker_name].get_account_info()
                capital = float(account.get("net_liquidation", 0.0) or 0.0)
            except Exception as e:  # noqa: BLE001
                log.debug(f"{broker_name}: could not fetch capital for EOD report: {e}")
                capital = 0.0

            log.info(f"{broker_name}: {len(rows)} open positions, gross notional {total_value:.2f}")
            for sym, side, qty, price, notional in sorted(rows, key=lambda r: -r[4]):
                pct = (notional / capital * 100) if capital > 0 else 0.0
                log.info(
                    f"  {sym:12s} {side:5s} qty={qty:<12.6f} "
                    f"price={price:<12.4f} notional={notional:>10.2f} ({pct:5.1f}% NAV)"
                )
        log.info("=== End of EOD risk report ===")
    def _apply_overnight_cap(self):
        """Apply overnight position size caps by reducing oversized positions."""
        log.info("=== Applying overnight position caps ===")
        for broker_name, pm in self.position_managers.items():
            if not pm.positions:
                continue

            latest_prices = self.broker_latest_prices.get(broker_name, {})
            try:
                account = self.broker_manager.brokers[broker_name].get_account_info()
                capital = float(account.get("net_liquidation", 0.0) or 0.0)
            except Exception as e:  # noqa: BLE001
                log.debug(f"{broker_name}: could not fetch capital for overnight cap: {e}")
                capital = 0.0

            if capital <= 0:
                continue

            # Get overnight cap threshold from config (default 0.20 = 20%)
            risk_cfg = self.config.get("risk_management", {})
            overnight_cap_threshold = risk_cfg.get("overnight_cap_threshold", 0.20)

            for sym, pos in list(pm.positions.items()):
                price = latest_prices.get(sym, pos.entry_price)
                if price <= 0:
                    continue
                notional = pos.quantity * price
                position_pct = notional / capital

                if position_pct > overnight_cap_threshold:
                    # Calculate target value to reduce to
                    target_notional = capital * overnight_cap_threshold
                    excess_notional = notional - target_notional
                    reduce_qty = excess_notional / price

                    # For non-crypto, round to whole shares
                    if not self._is_crypto(sym):
                        reduce_qty = int(reduce_qty)
                        if reduce_qty <= 0:
                            continue

                    # Determine side to reduce (opposite of position)
                    reduce_side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                    log.info(
                        f"{broker_name}: {sym} position at {position_pct:.1%} NAV exceeds "
                        f"overnight cap {overnight_cap_threshold:.0%}. Reducing by {reduce_qty:.6f}"
                    )
                    
                    self._place_trade(
                        broker=self.broker_manager.brokers[broker_name],
                        pm=pm,
                        symbol=sym,
                        action=reduce_side,
                        quantity=reduce_qty,
                        last_price=price,
                        stop_loss=0.0,
                        atr=0.0,
                        vol_stop_mult=0.0,
                        strategy_name="Overnight Cap"
                    )
        log.info("=== Overnight position caps applied ===")

    def _apply_weekend_flatten(self):
        """Apply weekend position reduction by reducing oversized positions on Fridays."""
        # Only run on Friday (weekday 4 where Monday is 0)
        if datetime.now(timezone.utc).weekday() != 4:  # Not Friday
            return

        log.info("=== Applying weekend position flattening ===")
        for broker_name, pm in self.position_managers.items():
            if not pm.positions:
                continue

            latest_prices = self.broker_latest_prices.get(broker_name, {})
            try:
                account = self.broker_manager.brokers[broker_name].get_account_info()
                capital = float(account.get("net_liquidation", 0.0) or 0.0)
            except Exception as e:  # noqa: BLE001
                log.debug(f"{broker_name}: could not fetch capital for weekend flatten: {e}")
                capital = 0.0

            if capital <= 0:
                continue

            # Get weekend flatten thresholds from config
            risk_cfg = self.config.get("risk_management", {})
            weekend_flatten_threshold = risk_cfg.get("weekend_flatten_threshold", 0.25)  # 25%
            weekend_flatten_target = risk_cfg.get("weekend_flatten_target", 0.15)     # 15%

            for sym, pos in list(pm.positions.items()):
                price = latest_prices.get(sym, pos.entry_price)
                if price <= 0:
                    continue
                notional = pos.quantity * price
                position_pct = notional / capital

                if position_pct > weekend_flatten_threshold:
                    # Calculate target value to reduce to
                    target_notional = capital * weekend_flatten_target
                    excess_notional = notional - target_notional
                    reduce_qty = excess_notional / price

                    # For non-crypto, round to whole shares
                    if not self._is_crypto(sym):
                        reduce_qty = int(reduce_qty)
                        if reduce_qty <= 0:
                            continue

                    # Determine side to reduce (opposite of position)
                    reduce_side = "SELL" if pos.side == "BUY" else "BUY_TO_COVER"
                    log.info(
                        f"{broker_name}: {sym} position at {position_pct:.1%} NAV exceeds "
                        f"weekend threshold {weekend_flatten_threshold:.0%}. Reducing to "
                        f"{weekend_flatten_target:.0%} NAV by {reduce_qty:.6f}"
                    )
                    
                    self._place_trade(
                        broker=self.broker_manager.brokers[broker_name],
                        pm=pm,
                        symbol=sym,
                        action=reduce_side,
                        quantity=reduce_qty,
                        last_price=price,
                        stop_loss=0.0,
                        atr=0.0,
                        vol_stop_mult=0.0,
                        strategy_name="Weekend Flatten"
                    )
        log.info("=== Weekend position flattening applied ===")


    # ------------------------------------------------------------------
    # Scanners
    # ------------------------------------------------------------------
    def _run_stock_scanner(self):
        """Update IBKR symbols with fresh stock candidates."""
        if self.scanner is None:
            return
        log.info("Running stock scanner...")
        new_symbols = self.scanner.scan_stocks()
        if new_symbols:
            self.symbols_by_broker["ib"] = new_symbols
            self.config["trading"]["symbols"] = new_symbols
            log.success(f"IBKR symbols updated: {', '.join(new_symbols[:10])}...")
        else:
            log.warning("Stock scanner returned no symbols; IBKR watchlist unchanged.")

    @staticmethod
    def _is_crypto_broker(broker_name: str) -> bool:
        """Return True for broker names that should use crypto scanning."""
        return broker_name not in {"ib", "nse"}

    def _run_crypto_scanner_for_broker(self, broker_name: str):
        """Update a specific crypto broker's symbol list with fresh candidates."""
        if self.scanner is None:
            return
        log.info(f"Running crypto scanner for {broker_name}...")
        new_pairs = self.scanner.scan_crypto(exchange_name=broker_name)

        # Define core crypto symbols that should always be traded
        CORE_CRYPTO = {"BTC/USDT", "ETH/USDT", "BNB/USDT"}

        if new_pairs:
            # Get current symbols for this broker to preserve core symbols
            current_symbols = self.symbols_by_broker.get(broker_name, [])
            # Extract core symbols from current list
            core_symbols = [s for s in current_symbols if s in CORE_CRYPTO]
            # Merge: core symbols + new scanner results (excluding any core symbols that might be in new_pairs)
            merged = core_symbols + [p for p in new_pairs if p not in CORE_CRYPTO]
            # Remove duplicates while preserving order
            seen = set()
            deduped = []
            for symbol in merged:
                if symbol not in seen:
                    seen.add(symbol)
                    deduped.append(symbol)
            self.symbols_by_broker[broker_name] = deduped
            log.success(f"{broker_name} symbols updated: {', '.join(deduped[:5])}...")
        else:
            log.warning(
                f"Crypto scanner returned no symbols for {broker_name}; watchlist unchanged."
            )

    def _run_crypto_scanner(self):
        """Update all crypto broker symbols with fresh candidates."""
        for broker_name in list(self.risk_managers):
            if self._is_crypto_broker(broker_name):
                self._run_crypto_scanner_for_broker(broker_name)

    def _run_clone_monitor(self):
        """Run the repo clone monitor script."""
        try:
            from tools.clone_monitor import main

            main()
        except Exception as e:  # noqa: BLE001
            log.warning(f"Clone monitor failed: {e}")

    def _run_trending_scanner(self):
        """Update KuCoin symbols with CoinGecko trending coins."""
        if self.scanner is None:
            return
        log.info("Running trending scanner...")
        trending_pairs = TrendingScanner.trending_usdt_pairs()
        if trending_pairs:
            current = set(self.symbols_by_broker.get("kucoin", []))
            new_set = current | set(trending_pairs)
            self.symbols_by_broker["kucoin"] = list(new_set)[:25]
            log.success(
                f"KuCoin symbols updated with trending coins: {', '.join(trending_pairs[:5])}..."
            )
        else:
            log.warning("Trending scanner returned no symbols.")

    # NSE Report Methods - Removed as superseded by African market scanner with Mansa API
    # The NSE report methods (_run_nse_morning_report, _run_nse_midday_report, _run_nse_close_report)
    # have been removed because the African market scanner now handles NSE data via Mansa API.
    # The _disseminate_nse_report method is kept for use by African market reporting.

    def _disseminate_nse_report(self, report: str, report_type: str):
        """Send NSE report to configured channels."""
        # Discord NSE webhook
        if hasattr(self, 'discord') and self.discord:
            self.discord.send_nse_report(report)
        # Telegram NSE topic
        if hasattr(self, 'telegram') and self.telegram:
            self.telegram.send_nse_report(report)
        # Email
        if hasattr(self, 'email') and self.email:
            html_body = f"<pre style='font-family:monospace;'>{report}</pre>"
            self.email.send_message(
                subject=f"[IrieTrade] NSE {report_type.capitalize()} Report",
                body_html=html_body,
                category=self.email.CATEGORY_AFRICA,
            )

    def _capture_african_snapshots(self):
        """Capture daily snapshots for all enabled African exchanges."""
        if not self.african_history:
            return
        log.info("Capturing African market snapshots...")
        self.african_history.capture_all()
        log.info("African market snapshots captured")

    def _run_african_morning_reports(self):
        if self.african_scanner is None:
            return

        log.info("Generating African market morning reports...")
        briefs = {}
        failed = []

        for exchange in ["NSE", "JSE", "NGX", "GSE", "BRVM"]:
            try:
                report = self.african_scanner.generate_report(exchange)
                # Skip placeholder reports that indicate no data was fetched
                if "Data unavailable" in report or "temporarily unavailable" in report:
                    failed.append(exchange)
                    continue
                insight = self._african_insight(exchange, report)
                briefs[exchange] = {"report": report, "insight": insight}
                # Still post to Telegram topic for that exchange
                self.telegram.send_exchange_report(exchange, report)
                # Send to Discord
                if self.discord.enabled:
                    self.discord.send_nse_report(f"*{exchange} Market Report*\n{report}")
            except Exception as e:  # noqa: BLE001
                log.warning(f"African report failed for {exchange}: {e}")
                failed.append(exchange)

        if briefs:
            self.email.send_africa_brief(briefs)
            failed_str = '", "'.join(failed) if failed else "none"
            log.info(
                f'African briefs sent: {len(briefs)} exchanges (failed: "{failed_str}")'
            )
        else:
            log.warning(
                f'African briefs skipped — no data available for any exchange (failed: {", ".join(failed)})'
            )
    def _run_african_close_report(self):
        if self.african_scanner is None:
            return

        log.info("Generating African market close reports...")
        briefs = {}

        for exchange in ["NSE", "JSE", "NGX", "GSE", "BRVM"]:
            try:
                report = self.african_scanner.generate_report(exchange)
                insight = self._african_insight(exchange, report)
                briefs[exchange] = {"report": report, "insight": insight}
                # Still post to Telegram topic for that exchange
                self.telegram.send_exchange_report(exchange, report)
                # Send to Discord
                if self.discord.enabled:
                    self.discord.send_nse_report(f"*{exchange} Market Report*\n{report}")
            except Exception as e:  # noqa: BLE001
                log.warning(f"African report failed for {exchange}: {e}")

        if briefs:
            self.email.send_africa_brief(briefs)
            log.info(f"African briefs sent: {len(briefs)} exchanges in one email")

    def _african_insight(self, exchange: str, report: str) -> str:
        """Ask the LLM for a one-paragraph take on the exchange's top movers."""
        try:
            # Get AI configuration from environment
            api_key = os.getenv("AI_API_KEY")
            if not api_key:
                return ""

            api_url = os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions")
            model = os.getenv("AI_MODEL", "gpt-4o-mini")

            # Create payload similar to the /api/assistant endpoint
            payload = {
                "model": model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a conservative market analyst. Provide brief, factual insights "
                            "based only on the provided data. Do not invent or exaggerate information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"You are a conservative market analyst. Below is today's {exchange} brief. "
                            f"Write 2 sentences on which 1-2 tickers look strongest for a swing position, "
                            f"and why. Do not invent data. If the list doesn't show clear momentum, say so.\n\n"
                            f"{report}"
                        ),
                    },
                ],
            }

            # Import the AI request function locally to avoid circular imports
            from monitoring.api import _post_ai_request

            # Make the AI request
            result = _post_ai_request(api_url, api_key, payload)
            out = result["choices"][0]["message"]["content"].strip()
            return out if out else ""
        except Exception as e:  # noqa: BLE001
            log.debug(f"African insight LLM failed for {exchange}: {e}")
            return ""

    def _ensure_clock_sync(self):
        """Ensure system clock is synchronized to prevent broker rejections."""
        import subprocess
        try:
            subprocess.run(["sudo", "systemctl", "restart", "systemd-timesyncd"], check=False, timeout=10)
            log.debug("Clock synchronization completed")
        except (subprocess.SubprocessError, OSError) as e:
            log.debug(f"Clock resync failed: {e}")
class HealthStatus(BaseModel):
    status: str
    ibkr_connected: bool
    data_fresh: bool
    last_error: str | None = None
    uptime_sec: float


health_app = FastAPI()


@health_app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    ibkr_connected = check_ibkr_connection()
    data_fresh = is_data_fresh()
    last_error = get_last_error()
    uptime_sec = time.time() - START_TIME
    return HealthStatus(
        status="ok" if ibkr_connected and data_fresh else "degraded",
        ibkr_connected=ibkr_connected,
        data_fresh=data_fresh,
        last_error=last_error,
        uptime_sec=uptime_sec,
    )


def run_health_server() -> None:
    uvicorn.run(health_app, host="127.0.0.1", port=8001, log_level="error")


def check_ibkr_connection() -> bool:
    """Return True if the IBKR connection is alive."""
    if _engine_instance is None:
        return False
    try:
        for broker_name, broker in _engine_instance.broker_manager.iterate_all():
            if broker_name != "ib":
                continue
            if hasattr(broker, "isConnected"):
                return broker.isConnected()
            return _engine_instance.broker_available.get(broker_name, False)
    except (AttributeError, ConnectionError, RuntimeError) as exc:
        log.warning(f"IBKR health check failed: {exc}")
    return False


def is_data_fresh() -> bool:
    """Return True if market data is updating within the expected interval."""
    if _engine_instance is None:
        return False
    return bool(_engine_instance.broker_latest_prices)


def get_last_error() -> str | None:
    """Return the most recent error message, if any."""
    if _engine_instance is None:
        return None
    return getattr(_engine_instance, "last_error", None)


if __name__ == "__main__":
    engine = TradingEngine()
    try:
        engine.start()
    except KeyboardInterrupt:
        log.info("Shutdown signal received. Stopping bot...")
        engine.is_running = False