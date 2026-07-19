# tests/test_risk_manager.py
from risk.manager import RiskManager

def test_can_trade_under_limits():
    rm = RiskManager(100000.0)
    rm.daily_pnl = -4000
    assert rm.can_trade() is True

def test_daily_loss_limit():
    rm = RiskManager(100000.0)
    rm.daily_pnl = -6000
    assert rm.can_trade() is False

def test_drawdown_limit():
    rm = RiskManager(100000.0)
    rm.peak_capital = 100000.0
    rm.current_capital = 79000.0
    assert rm.can_trade() is False

def test_validate_order():
    rm = RiskManager(100000.0)
    valid, msg = rm.validate_order('AAPL', 'BUY', 100, 100.0, 99.0)
    assert valid is True

def test_excessive_risk():
    rm = RiskManager(100000.0)
    valid, msg = rm.validate_order('AAPL', 'BUY', 100000, 100.0, 90.0)
    assert valid is False