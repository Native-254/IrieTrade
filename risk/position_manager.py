# risk/position_manager.py
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime

@dataclass
class Position:
    symbol: str
    side: str          # 'BUY' (long) or 'SELL' (short)
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: Optional[float] = None
    stop_order_id: int = 0
    tp_order_id: int = 0
    entry_time: Optional[datetime] = None

class PositionManager:
    def __init__(self):
        self.positions: Dict[str, Position] = {}

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get_side(self, symbol: str) -> Optional[str]:
        pos = self.positions.get(symbol)
        return pos.side if pos else None

    def open_position(self, position: Position):
        self.positions[position.symbol] = position

    def close_position(self, symbol: str):
        self.positions.pop(symbol, None)

    def get_all_positions(self) -> Dict[str, Position]:
        return self.positions