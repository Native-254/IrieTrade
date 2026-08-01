# execution/broker.py
from abc import ABC, abstractmethod


class Broker(ABC):
    """Abstract base class for all broker integrations.

    Every broker must implement connect, get_account_info, place_order,
    cancel_order, and get_positions.  Optional bracket/stop methods have
    default stubs that raise NotImplementedError, so the engine can safely
    fall back to plain orders.
    """

    supports_bracket: bool = (
        True  # override in subclass to False if bracket orders unavailable
    )

    @abstractmethod
    def connect(self):
        """Establishes connection to the broker's API."""
        ...

    @abstractmethod
    def get_account_info(self) -> dict[str, object]:
        """Fetches account details like buying power, positions, etc."""
        ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> dict[str, object]:
        """Places a new order.  Returns a dict with at least 'order_id'."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancels an existing order."""
        ...

    @abstractmethod
    def get_positions(self) -> list:
        """Returns a list of current positions (dicts with symbol, quantity, avg_cost)."""
        ...

    # --- Optional methods with safe defaults ---

    def disconnect(self):
        """Optional: close any persistent connections."""

    @property
    def supports_shorting(self) -> bool:
        """Return True if this broker can legally / structurally short sell.
        Default is True; override for cash‑only accounts or crypto."""
        return True

    def is_shortable(self, symbol: str, quantity: int) -> bool:
        """Default: all symbols are shortable (override for real checks)."""
        return self.supports_shorting

    def update_stop_order(self, order_id, new_stop_price) -> int | None:
        """Update an existing stop-loss order.  Returns new order ID or None."""
        raise NotImplementedError("This broker does not support updating stop orders.")

    def wait_for_fill(self, order_id, timeout=30) -> dict:
        """Wait for an order to fill.  Returns dict with status, filled, avg_price."""
        raise NotImplementedError("This broker does not support fill polling.")

    def place_bracket_long(self, symbol, quantity, entry_price, stop_loss, take_profit):
        """Place a bracket (parent + stop + target) for a long entry."""
        raise NotImplementedError("Bracket orders not supported by this broker.")

    def place_bracket_short(
        self, symbol, quantity, entry_price, stop_loss, take_profit
    ):
        """Place a bracket for a short entry."""
        raise NotImplementedError("Bracket orders not supported by this broker.")