# execution/broker.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class Broker(ABC):
    """Abstract base class for broker-specific execution handlers."""

    @abstractmethod
    def connect(self):
        """Establishes connection to the broker's API."""
        pass

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Fetches account details like buying power, positions, etc."""
        pass

    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: int, order_type: str, limit_price: Optional[float] = None, stop_price: Optional[float] = None) -> Dict[str, Any]:
        """Places a new order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancels an existing order."""
        pass

    @abstractmethod
    def get_positions(self) -> list:
        """Returns a list of current positions."""
        pass