# execution/okx_broker.py
import ccxt
import os
import time
from typing import Dict, Any, Optional, Tuple
from utils.logger import log
from execution.broker import Broker

class OKXBroker(Broker):
    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.getenv('OKX_API_KEY', '')
        self.secret = os.getenv('OKX_SECRET', '')
        self.password = os.getenv('OKX_PASSPHRASE', '')   # OKX requires a passphrase
        self.testnet = self.config.get('testnet', True)
        self.exchange = None
        self.connected = False
        self.supports_bracket = False

    def connect(self):
        if self.connected:
            return
        params = {
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.password,
            'enableRateLimit': True,
        }
        if self.testnet:
            params['urls'] = {
                'api': 'https://www.okx.com/api/v5',
                'test': 'https://www.okx.com/api/v5',   # OKX uses the same URLs for testnet
            }
        self.exchange = ccxt.okx(params)  # type: ignore[arg-type]
        self.exchange.load_markets()
        self.connected = True
        log.success("Connected to OKX (testnet=%s)", self.testnet)

    def disconnect(self):
        self.connected = False

    def get_account_info(self) -> Dict[str, Any]:
        if not self.connected:
            self.connect()
        assert self.exchange is not None
        balance = self.exchange.fetch_balance()
        total = balance.get('total', {})
        usdt_value = 0.0
        for asset, amount in total.items():
            amt = float(str(amount)) if amount else 0.0
            if amt == 0.0:
                continue
            if asset == 'USDT':
                usdt_value += amt
            else:
                try:
                    ticker = self.exchange.fetch_ticker(f'{asset}/USDT')
                    last_price = float(str(ticker['last'])) if ticker.get('last') else 0.0
                    usdt_value += amt * last_price
                except Exception:
                    pass
        return {'net_liquidation': usdt_value, 'account': 'OKX', 'unrealized_pnl': 0.0}

    def place_order(self, symbol: str, side: str, quantity: int,
                    order_type: str = 'MKT', limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None) -> Dict[str, Any]:
        if not self.connected:
            self.connect()
        assert self.exchange is not None
        if order_type.upper() != 'MKT':
            raise NotImplementedError("Only market orders for OKX in Phase 1")
        if side.upper() == 'BUY':
            order = self.exchange.create_market_buy_order(symbol, quantity)
        else:
            order = self.exchange.create_market_sell_order(symbol, quantity)
        log.info(f"OKX order placed: {side} {quantity} {symbol}. ID: {order['id']}")
        return {
            'order_id': order['id'],
            'status': order['status'],
            'filled_quantity': order['filled'],
            'avg_price': order['average'],
        }

    def place_bracket_long(self, symbol: str, quantity: int, entry_price: float,
                           stop_price: float, take_profit: float) -> Tuple[Optional[int], Optional[int]]:
        raise NotImplementedError

    def place_bracket_short(self, symbol: str, quantity: int, entry_price: float,
                            stop_price: float, take_profit: float) -> Tuple[Optional[int], Optional[int]]:
        raise NotImplementedError

    def get_stop_order_id(self, parent_id: int) -> int:
        return 0

    def update_stop_order(self, order_id: int, new_stop: float) -> Optional[int]:
        return None

    def cancel_order(self, order_id: str) -> bool:
        if not self.connected:
            self.connect()
        assert self.exchange is not None
        try:
            self.exchange.cancel_order(order_id, symbol=None)
            return True
        except Exception as e:
            log.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_positions(self) -> list:
        if not self.connected:
            self.connect()
        assert self.exchange is not None
        balance = self.exchange.fetch_balance()
        positions = []
        for asset, amount in balance['total'].items():
            amt = float(str(amount)) if amount else 0.0
            if amt > 0.0:
                positions.append({'symbol': asset, 'quantity': amt, 'avg_cost': 0.0, 'market_value': 0.0})
        return positions

    def is_shortable(self, symbol: str, quantity: int) -> bool:
        return False

    def wait_for_fill(self, order_id: int, timeout: int = 30) -> dict:
        if not self.connected:
            self.connect()
        assert self.exchange is not None
        start = time.time()
        while time.time() - start < timeout:
            try:
                order = self.exchange.fetch_order(str(order_id), symbol=None)
                if order['status'] == 'closed':
                    return {'filled': order['filled'], 'avg_price': order['average'], 'status': 'Filled'}
                elif order['status'] in ('canceled', 'expired'):
                    return {'filled': 0, 'status': 'Cancelled'}
            except Exception:
                pass
            time.sleep(1)
        return {'filled': 0, 'status': 'Timeout'}