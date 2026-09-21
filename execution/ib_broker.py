# execution/ib_broker.py
import time
from decimal import ROUND_HALF_UP, Decimal

from ib_async import (
    CFD,  # noqa: F401
    IB,
    Contract,
    Forex,
    Future,
    LimitOrder,
    MarketOrder,
    Stock,
    StopOrder,
)

from execution.broker import Broker
from utils.config import CONFIG
from utils.logger import log


class IBBroker(Broker):
    def __init__(self):
        self.ib = IB()
        self.config = CONFIG["exchanges"]["ib"]
        self.connected = False
        self.is_margin = True  # assume margin until proven otherwise
        self._contract_cache: dict[str, Contract] = {}

    def connect(self):
        if self.connected:
            return
        try:
            self.ib.connect(
                host="127.0.0.1",
                port=self.config["port"],
                clientId=self.config["client_id"],
                account=self.config["account_id"],
            )
            self.connected = True
            log.success(f"Connected to IBKR. Account: {self.config['account_id']}")
            # Detect account type
            try:
                summary = self.ib.accountSummary(self.config["account_id"])
                for s in summary:
                    if s.tag == "AccountType":
                        self.is_margin = (s.value.upper() != "CASH")
                        log.info("IB account type: %s → is_margin=%s", s.value, self.is_margin)
                        break
            except Exception as e:  # noqa: BLE001
                log.warning("Could not determine IB account type: %s; assuming margin.", e)
        except Exception as e:
            log.error(f"Failed to connect to IBKR: {e}")
            raise

    @staticmethod
    def _round_to_tick(price: float, min_tick: float) -> float:
        if min_tick <= 0:
            return float(price)
        rounded = (
            Decimal(str(price)) / Decimal(str(min_tick))
        ).quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(str(min_tick))
        return float(rounded)

    def _get_min_tick(self, contract) -> float:
        try:
            details = self.ib.reqContractDetails(contract)
            ticks = [
                float(getattr(detail, "minTick", 0.0) or 0.0)
                for detail in details
            ]
            return next((tick for tick in ticks if tick > 0), 0.01)
        except Exception as e:  # noqa: BLE001
            log.debug(f"Could not fetch min tick for {contract}: {e}")
            return 0.01

    def _normalize_price(self, contract, price: float) -> float:
        return self._round_to_tick(float(price), self._get_min_tick(contract))

    def _make_contract(self, symbol: str) -> Contract:
        """Create appropriate contract based on symbol format.

        Rules:
        - Forex: "EUR.USD", "GBP.JPY" (dot-separated, 3-letter currencies) -> "EURUSD"
        - Metals as CFD: XAUUSD, XAGUSD, etc.
        - Oil / index futures: symbol like "CL" or "CL=F" (needs front-month resolution)
        - Indices (ES/NQ): same as oil
        - Otherwise → Stock
        """
        s = symbol.upper().strip()

        # Forex: "EUR.USD", "GBP.JPY" (dot-separated, 3-letter currencies)
        if "." in s and len(s.split(".")) == 2:
            base, quote = s.split(".")
            if len(base) == 3 and len(quote) == 3 and base.isalpha() and quote.isalpha():
                # ib_async Forex expects pair format like "EURUSD" (no dot)
                return Forex(base + quote)  # e.g., "EURUSD"

        # Metals as CFD (no expiry headache)
        if s in {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"}:
            # For CFDs, we'll use Stock contract but with appropriate exchange
            # XAUUSD, XAGUSD are typically traded as CFDs on IBKR
            return Stock(s, "SMART", "USD")

        # Oil / index futures — symbol like "CL" or "CL=F"
        s = s.removesuffix("=F")  # Remove =F suffix for processing

        # Futures that we want to trade
        futures_symbols = {"CL", "BZ", "NG", "GC", "SI", "ES", "NQ", "YM", "RTY"}
        if s in futures_symbols:
            return self._front_month_future(s)

        # Default: stock
        return Stock(symbol, "SMART", "USD")

    def _front_month_future(self, root: str) -> Contract:
        """Return the nearest non-expired futures contract for a root symbol."""
        # CME/ICE month codes: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun,
        #                     N=Jul, Q=Aug, U=Sep, V=Oct, X=Nov, Z=Dec
        month_codes = "FGHJKMNQUVXZ"

        # Get current month code
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        month = now.month
        year = now.year % 100  # YY format

        # Try current month first, then future months
        for i in range(12):  # Check current and next 11 months
            month_idx = (month - 1 + i) % 12
            year_offset = (month - 1 + i) // 12
            contract_year = year + year_offset
            month_code = month_codes[month_idx]

            # Construct future symbol (e.g., "CLZ6" for December 2026)
            future_symbol = f"{root}{month_code}{contract_year:02d}"

            try:
                # Try to create and qualify this contract
                contract = Future(future_symbol, self._get_exchange(root), "USD")
                # We don't qualify here to avoid excessive API calls - qualification happens in _get_contract
                return contract
            except Exception as e:  # noqa: BLE001
                log.debug(f"Front month future attempt failed for {future_symbol}: {e}")
                # If this month fails, try next month
                continue

        # Fallback: return the first in the list if all else fails
        return Future(f"{root}{month_codes[0]}{year:02d}", self._get_exchange(root), "USD")

    def _get_exchange(self, root: str) -> str:
        """Get the appropriate exchange for a futures root symbol."""
        exchange_map = {
            "CL": "NYMEX",   # Crude Oil
            "BZ": "NYMEX",   # Brent Crude
            "NG": "NYMEX",   # Natural Gas
            "GC": "COMEX",   # Gold
            "SI": "COMEX",   # Silver
            "ES": "CME",     # E-mini S&P 500
            "NQ": "CME",     # E-mini NASDAQ 100
            "YM": "CBOT",    # E-mini Dow Jones
            "RTY": "CME",    # E-mini Russell 2000
        }
        return exchange_map.get(root.upper(), "SMART")

    @property
    def supports_shorting(self) -> bool:
        return self.is_margin

    def get_account_info(self) -> dict[str, object]:
        if not self.connected:
            self.connect()
        account_values = self.ib.accountValues(self.config["account_id"])
        net_liquidation = next(
            (float(v.value) for v in account_values if v.tag == "NetLiquidation"), 0.0
        )
        unrealized_pnl = next(
            (float(v.value) for v in account_values if v.tag == "UnrealizedPnL"), 0.0
        )
        return {
            "net_liquidation": net_liquidation,
            "account": self.config["account_id"],
            "unrealized_pnl": unrealized_pnl,
        }

    def _get_contract(self, symbol: str) -> Contract:
        """Get contract for symbol with caching."""
        if symbol not in self._contract_cache:
            contract = self._make_contract(symbol)
            self.ib.qualifyContracts(contract)
            self._contract_cache[symbol] = contract
            log.debug(f"Contract resolved: {symbol} → {contract}")
        return self._contract_cache[symbol]

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MKT",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict[str, object]:
        """Place an order, translating internal action names to IBKR sides."""
        if not self.connected:
            self.connect()

        # Map IrieTrade's internal actions to IBKR sides
        ib_side = side.upper()
        if ib_side == "BUY_TO_COVER":
            ib_side = "BUY"
        elif ib_side == "SELL_SHORT":
            ib_side = "SELL"

        contract = self._get_contract(symbol)
        if not self._safe_qualify(contract):
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        if order_type.upper() == "MKT":
            order = MarketOrder(ib_side, quantity)
            order.tif = "IOC"
        elif order_type.upper() == "LMT":
            if limit_price is None:
                raise ValueError("Limit price required for LMT order")
            limit_price = self._normalize_price(contract, limit_price)
            order = LimitOrder(ib_side, quantity, limit_price)
        else:
            raise ValueError(f"Unsupported order type: {order_type}")
        trade = self.ib.placeOrder(contract, order)
        log.info(
            f"Order placed: {side} ({ib_side}) {quantity} {symbol} @ {order_type}. ID: {trade.order.orderId}"
        )
        self.ib.sleep(1)
        return {
            "order_id": trade.order.orderId,
            "status": trade.orderStatus.status,
            "filled_quantity": trade.orderStatus.filled,
            "avg_price": trade.orderStatus.avgFillPrice,
        }

    def place_bracket_short(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop_price: float,
        take_profit: float,
    ) -> tuple[int | None, int | None]:
        if not self.connected:
            self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)
        stop_price = self._normalize_price(contract, stop_price)
        take_profit = self._normalize_price(contract, take_profit)
        parent = MarketOrder("SELL", quantity)
        parent.tif = "IOC"
        parent.transmit = False
        stop = StopOrder("BUY", quantity, stop_price)
        stop.tif = "DAY"
        stop.transmit = False
        tp = LimitOrder("BUY", quantity, take_profit)
        tp.tif = "DAY"
        tp.transmit = True
        parent_trade = self.ib.placeOrder(contract, parent)
        stop_trade = self.ib.placeOrder(contract, stop)
        self.ib.placeOrder(contract, tp)
        self.ib.sleep(1)
        parent_id = getattr(parent_trade.order, "orderId", None)
        stop_id = getattr(stop_trade.order, "orderId", None)
        log.info(
            f"Placed bracket short for {symbol}: parent_id={parent_id}, stop_id={stop_id}"
        )
        return parent_id, stop_id

    def place_bracket_long(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        stop_price: float,
        take_profit: float,
    ) -> tuple[int | None, int | None]:
        if not self.connected:
            self.connect()
        contract = self._get_contract(symbol)
        self.ib.qualifyContracts(contract)
        stop_price = self._normalize_price(contract, stop_price)
        take_profit = self._normalize_price(contract, take_profit)
        parent = MarketOrder("BUY", quantity)
        parent.tif = "IOC"
        parent.transmit = False
        stop = StopOrder("SELL", quantity, stop_price)
        stop.tif = "DAY"
        stop.transmit = False
        tp = LimitOrder("SELL", quantity, take_profit)
        tp.tif = "DAY"
        tp.transmit = True
        parent_trade = self.ib.placeOrder(contract, parent)
        stop_trade = self.ib.placeOrder(contract, stop)
        self.ib.placeOrder(contract, tp)
        self.ib.sleep(1)
        parent_id = getattr(parent_trade.order, "orderId", None)
        stop_id = getattr(stop_trade.order, "orderId", None)
        log.info(
            f"Placed bracket long for {symbol}: parent_id={parent_id}, stop_id={stop_id}"
        )
        return parent_id, stop_id

    def get_stop_order_id(self, parent_id: int) -> int:
        for trade in self.ib.trades():
            if (
                getattr(trade.order, "parentId", None) == parent_id
                and getattr(trade.order, "orderType", None) == "STP"
            ):
                return trade.order.orderId
        return 0

    def update_stop_order(self, order_id: int, new_stop: float) -> int | None:
        if not self.connected:
            self.connect()
        for trade in self.ib.trades():
            if trade.order.orderId == order_id and trade.order.orderType == "STP":
                self.ib.cancelOrder(trade.order)
                new_stop = self._normalize_price(trade.contract, new_stop)
                new_order = StopOrder(
                    trade.order.action, trade.order.totalQuantity, new_stop, tif="DAY"
                )
                new_trade = self.ib.placeOrder(trade.contract, new_order)
                self.ib.sleep(1)
                new_order_id = getattr(new_trade.order, "orderId", None)
                log.info(
                    f"Updated stop order {order_id} -> {new_order_id} at {new_stop}"
                )
                return new_order_id
        log.warning(f"Stop order {order_id} not found.")
        return None

    def cancel_order(self, order_id: str) -> bool:
        if not self.connected:
            self.connect()
        for trade in self.ib.trades():
            if str(trade.order.orderId) == str(order_id):
                self.ib.cancelOrder(trade.order)
                log.info(f"Order {order_id} cancelled.")
                return True
        log.warning(f"Order {order_id} not found.")
        return False

    def get_positions(self) -> list:
        if not self.connected:
            self.connect()
        positions = []
        for pos in self.ib.positions():
            positions.append(
                {
                    "symbol": pos.contract.symbol,
                    "quantity": pos.position,
                    "avg_cost": getattr(pos, "avgCost", 0.0),
                    "market_value": getattr(pos, "marketValue", 0.0),
                }
            )
        return positions

    def is_shortable(self, symbol: str, quantity: float) -> bool:
        if not self.connected:
            self.connect()
        if not self.supports_shorting:
            log.info(f"Short sale of {symbol} blocked – cash account.")
            return False
        try:
            contract = self._get_contract(symbol)
            self.ib.qualifyContracts(contract)
            shortable_func = getattr(self.ib, "shortableShares", None)
            if shortable_func is None:
                log.warning(f"Shortable check unsupported by IB API for {symbol}.")
                return True
            details = shortable_func(contract)
            if details and details.get("shortable", 0) >= quantity:
                return True
            log.warning(
                f"Short sale of {quantity} {symbol} not allowed or insufficient shares."
            )
            return False
        except Exception as e:  # noqa: BLE001
            log.error(f"Shortable check failed for {symbol}: {e}")
            return False

    def wait_for_fill(self, order_id: int, timeout: int = 30) -> dict:
        if not self.connected:
            self.connect()
        start = time.time()
        while time.time() - start < timeout:
            for trade in self.ib.trades():
                if trade.order.orderId == order_id:
                    status = trade.orderStatus.status
                    if status == "Filled":
                        return {
                            "filled": trade.orderStatus.filled,
                            "avg_price": trade.orderStatus.avgFillPrice,
                            "status": status,
                        }
                    elif status in ("Cancelled", "Inactive", "ApiCancelled"):
                        return {"filled": 0, "status": status}
            time.sleep(0.5)
        return {"filled": 0, "status": "Timeout"}

    def cancel_all_for_symbol(self, symbol: str) -> int:
        """Cancel every open order for the given symbol. Returns count cancelled."""
        if not self.connected:
            self.connect()
        count = 0
        for trade in self.ib.trades():
            try:
                if trade.contract.symbol != symbol:
                    continue
                if trade.orderStatus.status in (
                    "PreSubmitted",
                    "Submitted",
                    "PendingSubmit",
                ):
                    self.ib.cancelOrder(trade.order)
                    count += 1
            except Exception as e:  # noqa: BLE001
                log.debug(f"Failed cancelling an order for {symbol}: {e}")
        if count:
            log.info(f"Cancelled {count} pending order(s) for {symbol}.")
        return count

    def _safe_qualify(self, contract) -> bool:
        """Qualify a contract, reconnecting if the socket dropped."""
        for attempt in range(2):
            try:
                self.ib.qualifyContracts(contract)
                return True
            except Exception as e:  # noqa: BLE001
                log.warning(f"qualifyContracts failed (attempt {attempt+1}): {e}")
                self.connected = False
                if attempt == 0:
                    time.sleep(2)
                    self.connect()
        return False

    def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            self._contract_cache.clear()
            log.info("Disconnected from IBKR.")
