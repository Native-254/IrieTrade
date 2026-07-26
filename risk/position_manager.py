# risk/position_manager.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Position:
    symbol: str
    side: str          # 'BUY' (long) or 'SELL' (short)
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float | None = None
    stop_order_id: int = 0
    tp_order_id: int = 0
    entry_time: datetime | None = None


class PositionManager:
    def __init__(self):
        self.positions: dict[str, Position] = {}

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get_side(self, symbol: str) -> str | None:
        pos = self.positions.get(symbol)
        return pos.side if pos else None

    def open_position(self, position: Position):
        self.positions[position.symbol] = position

    def close_position(self, symbol: str):
        self.positions.pop(symbol, None)

    def get_all_positions(self) -> dict[str, Position]:
        return self.positions