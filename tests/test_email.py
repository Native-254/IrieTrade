# tests/test_email.py
import types

import live.engine as engine_module
from monitoring.email_alerter import EmailAlerter


class DummyEmail(EmailAlerter):
    def __init__(self):
        self.trade_calls = []
        self.error_calls = []

    def send_trade_alert(self, *args):
        self.trade_calls.append(args)

    def send_error_alert(self, *args):
        self.error_calls.append(args)


# Minimal config that mimics a single broker setup for testing.
def minimal_config():
    return {
        "trading": {
            "symbols": ["AAPL"],
            "symbols_by_broker": {"test_broker": ["AAPL"]},
        },
        "strategies": {"active": [], "parameters": {}},
        "risk_management": {
            "volatility_stop_multiplier": 1.0,
            "max_gross_exposure": 999.0,
            "max_net_exposure": 999.0,
            "max_position_pct": 1.0,
        },
        "execution": {
            "simulate_slippage": False,
            "simulate_commissions": False,
            "simulate_partial_fills": False,
            "short_availability_check": False,
            "earnings_avoidance": False,
        },
        "general": {"bot_name": "TestBot"},
        "monitoring": {"health_check_port": 9999},
    }


def test_place_trade_sends_trade_email_on_success(monkeypatch):
    # Create engine with our config
    engine = engine_module.TradingEngine(config=minimal_config())

    # Replace its email alerter with our dummy
    email_stub = DummyEmail()
    engine.email = email_stub

    # We'll test using the first broker (index 0).  For simplicity, iterate_all gives names and brokers.
    broker_name, broker = next(engine.broker_manager.iterate_all())
    pm = engine.position_managers[broker_name]
    # Configure broker to succeed
    broker.connect = types.MethodType(lambda self: None, broker)
    broker.disconnect = types.MethodType(lambda self: None, broker)
    broker.supports_bracket = True
    broker.place_bracket_long = types.MethodType(
        lambda self, symbol, qty, entry, stop, tp: ("order1", "stop1"), broker
    )
    broker.wait_for_fill = types.MethodType(
        lambda self, order_id: {"status": "Filled", "filled": 10, "avg_price": 100.0},
        broker,
    )

    # Required args for _place_trade: broker, pm, symbol, action, qty, last_price, stop_loss, atr, vol_stop_mult
    success = engine._place_trade(
        broker=broker,
        pm=pm,
        symbol="AAPL",
        action="BUY",
        quantity=10,
        last_price=100.0,
        stop_loss=95.0,
        atr=1.0,
        vol_stop_mult=1.0,
    )
    assert success is True
    # The email alerter should have been called
    assert email_stub.trade_calls == [("AAPL", "BUY", 10, 100.0)]


def test_place_trade_sends_error_email_on_no_position(monkeypatch):
    engine = engine_module.TradingEngine(config=minimal_config())
    email_stub = DummyEmail()
    engine.email = email_stub

    broker_name, broker = next(engine.broker_manager.iterate_all())
    pm = engine.position_managers[broker_name]
    # Make broker fail later if needed, but here we test exit scenario with no position
    broker.connect = types.MethodType(lambda self: None, broker)
    broker.disconnect = types.MethodType(lambda self: None, broker)
    # We'll call with an exit action but no position in pm
    pm.positions = {}  # ensure empty

    # Suppress logging to avoid console noise
    monkeypatch.setattr(
        engine_module,
        "log",
        types.SimpleNamespace(
            error=lambda *a, **kw: None,
            warning=lambda *a, **kw: None,
            exception=lambda *a, **kw: None,
            info=lambda *a, **kw: None,
        ),
    )

    success = engine._place_trade(
        broker=broker,
        pm=pm,
        symbol="AAPL",
        action="SELL",
        quantity=10,
        last_price=100.0,
        stop_loss=95.0,
        atr=1.0,
        vol_stop_mult=1.0,
    )
    assert success is False
    # The error alert should contain the "no internal position" message
    assert any("no internal position" in msg[0] for msg in email_stub.error_calls)
