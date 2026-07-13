from types import SimpleNamespace

import live.engine as engine_module
from monitoring.email_alerter import EmailAlerter


class DummyEmail(EmailAlerter):
    def __init__(self):
        self.trade_calls: list[tuple] = []
        self.error_calls: list[tuple] = []

    def send_trade_alert(self, *args):
        self.trade_calls.append(args)

    def send_error_alert(self, *args):
        self.error_calls.append(args)


def build_engine(**overrides):
    broker = SimpleNamespace(
        connect=lambda: None,
        disconnect=lambda: None,
        place_order=lambda **kwargs: {"status": "Filled", "avg_price": 100.0},
        ib=SimpleNamespace(sleep=lambda _: None),
        get_account_info=lambda: {"net_liquidation": 1000.0},
    )
    position_manager = SimpleNamespace(
        positions={"AAPL": SimpleNamespace(entry_price=90.0, side="BUY", quantity=10)},
        open_position=lambda pos: None,
        close_position=lambda symbol: None,
    )
    config = {
        "strategies": {"active": [], "parameters": {"intraday": {}}},
        "risk_management": {"volatility_stop_multiplier": 1.0},
        "general": {"bot_name": "Test Bot"},
        "monitoring": {"health_check_port": 8000},
    }

    return engine_module.TradingEngine(
        broker=overrides.get("broker", broker),
        data_manager=overrides.get("data_manager", SimpleNamespace()),
        telegram=overrides.get("telegram", SimpleNamespace()),
        discord=overrides.get("discord", SimpleNamespace()),
        email=overrides.get("email", DummyEmail()),
        position_manager=overrides.get("position_manager", position_manager),
        risk_manager=overrides.get("risk_manager", SimpleNamespace()),
        config=overrides.get("config", config),
    )


def test_place_trade_sends_trade_email_on_success():
    email_stub = DummyEmail()
    engine = build_engine(email=email_stub)
    engine.trade_results = []

    assert engine._place_trade("AAPL", "SELL", 10, 100.0, 95.0, 1.0, 1.0) is True
    assert email_stub.trade_calls == [("AAPL", "SELL", 10, 100.0)]


def test_place_trade_sends_error_email_on_failure(monkeypatch):
    email_stub = DummyEmail()
    engine = build_engine(email=email_stub)
    engine.position_manager = SimpleNamespace(positions={}, close_position=lambda symbol: None)
    engine.broker = SimpleNamespace(connect=lambda: None, disconnect=lambda: None)

    monkeypatch.setattr(engine_module, "log", SimpleNamespace(error=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None))

    assert engine._place_trade("AAPL", "SELL", 10, 100.0, 95.0, 1.0, 1.0) is False
    assert email_stub.error_calls == [("Trade failed for AAPL: no internal position",)]
