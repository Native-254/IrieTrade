# execution/broker.py
from abc import ABC, abstractmethod


class Broker(ABC):
    """Abstract base class for broker-specific execution handlers."""

    @abstractmethod
    def connect(self):
        """Establishes connection to the broker's API."""
        ...

    @abstractmethod
    def get_account_info(self) -> dict[str, object]:
        """Fetches account details like buying power, positions, etc."""
        ...

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: int, order_type: str,
                    limit_price: float | None = None, stop_price: float | None = None) -> dict[str, object]:
        """Places a new order."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancels an existing order."""
        ...

    @abstractmethod
    def get_positions(self) -> list:
        """Returns a list of current positions."""
        ...