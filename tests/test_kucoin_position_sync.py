from live.engine import TradingEngine
from risk.position_manager import PositionManager


class FakeBroker:
    """Minimal broker stub returning KuCoin‑style positions."""

    def __init__(self, positions: list[dict]):
        self._positions = positions

    def get_positions(self) -> list[dict]:
        return self._positions


def test_sync_positions_from_broker_loads_crypto_pairs():
    engine = object.__new__(TradingEngine)  # skip __init__, we only need the method
    pm = PositionManager()

    broker = FakeBroker(
        [
            {"symbol": "DOT/USDT", "quantity": 10.5, "avg_cost": 0.0},
            {"symbol": "BTC/USDT", "quantity": 0.001, "avg_cost": 0.0},
        ]
    )

    engine._sync_positions_from_broker(broker, pm)

    assert "DOT/USDT" in pm.positions
    assert pm.positions["DOT/USDT"].quantity == 10.5
    assert pm.positions["DOT/USDT"].entry_price == 0.0
    assert "BTC/USDT" in pm.positions
    assert pm.positions["BTC/USDT"].quantity == 0.001
    assert pm.positions["BTC/USDT"].entry_price == 0.0
    