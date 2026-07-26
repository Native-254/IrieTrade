# execution/nse_broker.py
"""
Nairobi Securities Exchange (NSE) broker stub.
Placeholder for future API integration – currently raises NotImplementedError
for trading operations but returns a dummy account state.
"""

from execution.broker import Broker


class NSEBroker(Broker):
    """Stub broker for the Nairobi Securities Exchange."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.connected = False

    def connect(self):
        """Pretend to connect."""
        self.connected = True

    def disconnect(self):
        self.connected = False

    def get_account_info(self) -> dict[str, object]:
        """Returns a dummy KES account for dashboard display."""
        return {
            "net_liquidation": 1_000_000.0,  # KES
            "currency": "KES",
            "available_funds": 1_000_000.0,
            "buying_power": 2_000_000.0,
        }

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError("NSE live trading is not yet available.")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("NSE live trading is not yet available.")

    def get_positions(self) -> list:
        """No positions in this stub."""
        return []

    def is_shortable(self, symbol: str, quantity: int) -> bool:
        """Short selling is not supported on the NSE."""
        return False

    def update_stop_order(self, order_id, new_stop_price) -> int | None:
        raise NotImplementedError("NSE stop orders not yet implemented.")

    def wait_for_fill(self, order_id, timeout=30) -> dict:
        raise NotImplementedError("NSE order fill monitoring not available.")

    def place_bracket_long(self, symbol, quantity, entry_price, stop_loss, take_profit):
        raise NotImplementedError("NSE bracket orders not supported.")

    def place_bracket_short(
        self, symbol, quantity, entry_price, stop_loss, take_profit
    ):
        raise NotImplementedError("NSE bracket orders not supported.")

    def supports_bracket(self) -> bool:
        return False
