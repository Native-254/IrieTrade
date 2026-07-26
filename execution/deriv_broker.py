# execution/deriv_broker.py
import os
import time

import requests

from execution.broker import Broker
from utils.logger import log


class DerivBroker(Broker):
    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.getenv("DERIV_API_KEY", "")
        self.base_url = config.get("base_url", "https://api.deriv.com")
        self.connected = False
        self.supports_bracket = False

    def connect(self):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self.connected = True
        log.success("Connected to Deriv (REST)")

    def disconnect(self):
        self.connected = False

    def get_account_info(self) -> dict[str, object]:
        if not self.connected:
            self.connect()
        resp = self.session.get(f"{self.base_url}/account")
        if resp.status_code == 200:
            data = resp.json()
            return {
                "net_liquidation": float(data.get("balance", 0)),
                "account": "Deriv",
                "unrealized_pnl": 0.0,
            }
        return {"net_liquidation": 0.0, "account": "Deriv", "unrealized_pnl": 0.0}

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MKT",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict[str, object]:
        if not self.connected:
            self.connect()
        payload = {
            "symbol": symbol,
            "side": side.lower(),
            "amount": quantity,
            "type": "market" if order_type.upper() == "MKT" else "limit",
        }
        resp = self.session.post(f"{self.base_url}/order", json=payload)
        if resp.status_code == 200:
            order = resp.json()
            return {
                "order_id": order["id"],
                "status": "filled" if order.get("filled") else "open",
                "filled_quantity": order.get("filled", 0),
                "avg_price": order.get("avg_price", 0),
            }
        return {"order_id": None, "status": "rejected"}

    def wait_for_fill(self, order_id: int, timeout: int = 30) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            resp = self.session.get(f"{self.base_url}/order/{order_id}")
            if resp.status_code == 200:
                order = resp.json()
                if order.get("status") == "filled":
                    return {
                        "filled": order["filled"],
                        "avg_price": order["avg_price"],
                        "status": "Filled",
                    }
                elif order.get("status") == "cancelled":
                    return {"filled": 0, "status": "Cancelled"}
            time.sleep(1)
        return {"filled": 0, "status": "Timeout"}

    def place_bracket_long(self, *args):
        raise NotImplementedError

    def place_bracket_short(self, *args):
        raise NotImplementedError

    def get_stop_order_id(self, parent_id: int) -> int:
        return 0

    def update_stop_order(self, order_id: int, new_stop: float) -> int | None:
        return None

    def cancel_order(self, order_id: str) -> bool:
        return False

    def get_positions(self) -> list:
        return []

    def is_shortable(self, symbol: str, quantity: float) -> bool:
        return False
