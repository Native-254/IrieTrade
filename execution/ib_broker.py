# execution/ib_broker.py
import time
from ib_async import IB, Stock, MarketOrder, LimitOrder, StopOrder
from typing import Dict, Any, Optional
from utils.config import CONFIG
from utils.logger import log
from execution.broker import Broker

class IBBroker(Broker):
    def __init__(self):
        self.ib = IB()
        self.config = CONFIG['exchanges']['nyse']
        self.connected = False

    def connect(self):
        """Connects to TWS or IB Gateway."""
        if self.connected:
            return
        try:
            self.ib.connect(
                host='127.0.0.1',
                port=self.config['port'],
                clientId=self.config['client_id'],
                account=self.config['account_id']
            )
            self.connected = True
            log.success(f"Connected to IBKR. Account: {self.config['account_id']}")
        except Exception as e:
            log.error(f"Failed to connect to IBKR: {e}")
            raise

    def get_account_info(self) -> Dict[str, Any]:
        """Fetches account summary."""
        if not self.connected:
            self.connect()
        account_values = self.ib.accountValues(self.config['account_id'])
        net_liquidation = next((float(v.value) for v in account_values if v.tag == 'NetLiquidation'), 0.0)
        unrealized_pnl = next((float(v.value) for v in account_values if v.tag == 'UnrealizedPnL'), 0.0)
        return {
            'net_liquidation': net_liquidation,
            'account': self.config['account_id'],
            'unrealized_pnl': unrealized_pnl,
        }

    def place_order(self, symbol: str, side: str, quantity: int, order_type: str = 'MKT', limit_price: Optional[float] = None, stop_price: Optional[float] = None) -> Dict[str, Any]:
        """Places an order with IBKR."""
        if not self.connected:
            self.connect()

        # Create a contract for US stocks
        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)

        # Create order object
        if order_type.upper() == 'MKT':
            order = MarketOrder(side.upper(), quantity)
        elif order_type.upper() == 'LMT':
            if limit_price is None:
                raise ValueError("Limit price required for LMT order")
            order = LimitOrder(side.upper(), quantity, limit_price)
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        # Place the order
        trade = self.ib.placeOrder(contract, order)
        log.info(f"Order placed: {side} {quantity} {symbol} @ {order_type}. ID: {trade.order.orderId}")

        # Wait for order to be submitted
        self.ib.sleep(1)
        return {
            'order_id': trade.order.orderId,
            'status': trade.orderStatus.status,
            'filled_quantity': trade.orderStatus.filled,
            'avg_price': trade.orderStatus.avgFillPrice
        }

    def place_bracket_short(self, symbol: str, quantity: int, entry_price: float,
                            stop_price: float, take_profit: float):
        """Places a short (sell) parent market order with attached stop-loss and take-profit (bracket) orders."""
        if not self.connected:
            self.connect()

        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)

        # Parent order (sell short)
        parent = MarketOrder('SELL', quantity)
        parent.tif = 'DAY'
        parent.transmit = False

        # Stop-loss order (buy to cover if price rises to stop_price)
        stop = StopOrder('BUY', quantity, stop_price)
        stop.tif = 'DAY'
        stop.transmit = False

        # Take-profit order (buy to cover if price falls to take_profit)
        tp = LimitOrder('BUY', quantity, take_profit)
        tp.tif = 'DAY'
        tp.transmit = True

        # Place orders: parent first (not transmitted), then child orders
        parent_trade = self.ib.placeOrder(contract, parent)
        self.ib.placeOrder(contract, stop)
        self.ib.placeOrder(contract, tp)

        # Allow a moment for IB to assign an orderId
        self.ib.sleep(1)

        parent_id = getattr(parent_trade.order, 'orderId', None)
        log.info(f"Placed bracket short for {symbol}: parent_id={parent_id}")
        return parent_id

    def place_bracket_long(self, symbol: str, quantity: int, entry_price: float,
                           stop_price: float, take_profit: float) -> Optional[int]:
        """Places a long (buy) parent market order with attached stop-loss and take-profit (bracket) orders."""
        if not self.connected:
            self.connect()

        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)

        parent = MarketOrder('BUY', quantity)
        parent.tif = 'DAY'
        parent.transmit = False

        stop = StopOrder('SELL', quantity, stop_price)
        stop.tif = 'DAY'
        stop.transmit = False

        tp = LimitOrder('SELL', quantity, take_profit)
        tp.tif = 'DAY'
        tp.transmit = True

        parent_trade = self.ib.placeOrder(contract, parent)
        self.ib.placeOrder(contract, stop)
        self.ib.placeOrder(contract, tp)

        self.ib.sleep(1)

        parent_id = getattr(parent_trade.order, 'orderId', None)
        log.info(f"Placed bracket long for {symbol}: parent_id={parent_id}")
        return parent_id

    def get_stop_order_id(self, parent_id: int) -> int:
        """Return the stop-order child ID for a bracket parent."""
        for trade in self.ib.trades():
            if getattr(trade.order, 'parentId', None) == parent_id and getattr(trade.order, 'orderType', None) == 'STP':
                return trade.order.orderId
        return 0

    def update_stop_order(self, order_id: int, new_stop: float):
        """Cancel old stop order and replace with a new one."""
        if not self.connected:
            self.connect()

        for trade in self.ib.trades():
            if trade.order.orderId == order_id and trade.order.orderType == 'STP':
                self.ib.cancelOrder(trade.order)

                new_order = StopOrder(
                    trade.order.action,
                    trade.order.totalQuantity,
                    new_stop,
                    tif='DAY'
                )
                new_trade = self.ib.placeOrder(trade.contract, new_order)
                self.ib.sleep(1)

                new_order_id = getattr(new_trade.order, 'orderId', None)
                log.info(f"Updated stop order {order_id} -> {new_order_id} at {new_stop}")
                return new_order_id

        log.warning(f"Stop order {order_id} not found.")
        return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancels an order by ID."""
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
        """Returns a list of current positions."""
        if not self.connected:
            self.connect()
        positions = []
        for pos in self.ib.positions():
            positions.append({
                'symbol': pos.contract.symbol,
                'quantity': pos.position,
                'avg_cost': getattr(pos, 'avgCost', 0.0),
                'market_value': getattr(pos, 'marketValue', 0.0)
            })
        return positions

    def is_shortable(self, symbol: str, quantity: int) -> bool:
        """Check if there are enough shares to short."""
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.qualifyContracts(contract)
            shortable_func = getattr(self.ib, 'shortableShares', None)
            if shortable_func is None:
                log.warning(f"Shortable check unsupported by IB API for {symbol}.")
                return True
            details = shortable_func(contract)
            if details and details.get('shortable', 0) >= quantity:
                return True
            log.warning(f"Short sale of {quantity} {symbol} not allowed or insufficient shares.")
            return False
        except Exception as e:
            log.error(f"Shortable check failed for {symbol}: {e}")
            return False

    def wait_for_fill(self, order_id: int, timeout: int = 30) -> dict:
        """Wait for an order to fill and return fill details."""
        start = time.time()
        while time.time() - start < timeout:
            for trade in self.ib.trades():
                if trade.order.orderId == order_id:
                    status = trade.orderStatus.status
                    if status == 'Filled':
                        return {
                            'filled': trade.orderStatus.filled,
                            'avg_price': trade.orderStatus.avgFillPrice,
                            'status': status,
                        }
                    elif status in ('Cancelled', 'Inactive', 'ApiCancelled'):
                        return {'filled': 0, 'status': status}
            time.sleep(0.5)
        return {'filled': 0, 'status': 'Timeout'}

    def disconnect(self):
        """Cleanly disconnect."""
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            log.info("Disconnected from IBKR.")