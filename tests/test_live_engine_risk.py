from live.engine import TradingEngine
from monitoring.email_alerter import EmailAlerter
from risk.manager import RiskManager
from risk.position_manager import Position, PositionManager


class _EmailStub(EmailAlerter):
    def send_error_alert(self, *_args, **_kwargs):
        pass
class _DustBrokerStub:
    supports_bracket = False

    def __init__(self):
        self.order_was_placed = False
        self.last_quantity = 0.0

    def get_min_order_notional(self, _symbol):
        return 0.1

    def connect(self):
        pass

    def disconnect(self):
        pass

    def place_order(self, *_args, **kwargs):
        self.order_was_placed = True
        self.last_quantity = float(kwargs.get("quantity", 0.0))
        return {"order_id": "unexpected"}

    def wait_for_fill(self, _order_id):
        return {"status": "Filled", "filled": self.last_quantity, "avg_price": 0.0}


def _engine_for_unit_tests():
    engine = object.__new__(TradingEngine)
    engine.config = {
        "execution": {
            "earnings_avoidance": False,
            "simulate_slippage": False,
            "simulate_partial_fills": False,
            "simulate_commissions": False,
        },
        "risk_management": {"max_net_exposure": 0.5},
        "rotation": {"enabled": False},
    }
    engine.email = _EmailStub()
    return engine


def test_net_exposure_allows_trade_that_reduces_existing_breach():
    engine = _engine_for_unit_tests()
    pm = PositionManager()
    pm.open_position(Position("SPY", "SELL", 1000, 100.0, float("inf")))
    rm = RiskManager(100_000.0, position_manager=pm)
    rm.config["max_net_exposure"] = 0.5

    assert engine._check_net_exposure(rm, "BUY", 400, 100.0, {}) is True


def test_net_exposure_rejects_trade_that_worsens_existing_breach():
    engine = _engine_for_unit_tests()
    pm = PositionManager()
    pm.open_position(Position("SPY", "SELL", 1000, 100.0, float("inf")))
    rm = RiskManager(100_000.0, position_manager=pm)
    rm.config["max_net_exposure"] = 0.5

    assert engine._check_net_exposure(rm, "SELL_SHORT", 1, 100.0, {}) is False


def test_crypto_dust_exit_is_removed_before_broker_order():
    engine = _engine_for_unit_tests()
    broker = _DustBrokerStub()
    pm = PositionManager()
    pm.open_position(Position("DOT/USDT", "BUY", 0.0005, 0.8, 0.0))

    result = engine._place_trade(
        broker,
        pm,
        "DOT/USDT",
        "SELL",
        0.0005,
        0.8,
        stop_loss=0.0,
        atr=0.0,
        vol_stop_mult=0.0,
    )

    assert result is False
    assert not pm.has_position("DOT/USDT")
    assert broker.order_was_placed is False


def test_enforce_risk_limits_reduces_single_name_breach():
    engine = _engine_for_unit_tests()
    broker = _DustBrokerStub()  # supports min notional, will not place unexpected orders
    pm = PositionManager()
    pm.open_position(Position("UNH", "BUY", 2000, 400.0, 0.0))
    rm = RiskManager(100_000.0, position_manager=pm)
    rm.config["max_position_pct"] = 0.25
    rm.config["max_gross_exposure"] = 2.5
    rm.config["max_net_exposure"] = 0.5

    engine._enforce_risk_limits(
        broker, pm, rm, {"UNH": 400.0}, 100_000.0
    )

    assert pm.positions["UNH"].quantity < 2000
    assert pm.positions["UNH"].quantity == 625  # 25% of 100k / 400 = 625


def test_rotate_underperformers_closes_losing_stale_position():
    engine = _engine_for_unit_tests()
    broker = _DustBrokerStub()
    pm = PositionManager()
    pm.open_position(Position("OLD", "BUY", 100, 50.0, 0.0))

    engine._rotate_underperformers(
        broker, pm, {"OLD": 40.0}, ["NEW"]
    )

    assert not pm.has_position("OLD")