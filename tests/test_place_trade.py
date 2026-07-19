# tests/test_place_trade.py
from unittest.mock import MagicMock
from live.engine import TradingEngine

def test_place_trade_buy_calls_broker():
    mock_broker = MagicMock()
    mock_broker.place_bracket_long.return_value = (1, 2)
    mock_broker.wait_for_fill.return_value = {'status': 'Filled', 'filled': 100, 'avg_price': 150.0}
    mock_broker.get_account_info.return_value = {'net_liquidation': 100000.0}
    mock_email = MagicMock()
    mock_telegram = MagicMock()
    mock_discord = MagicMock()
    mock_pos_mgr = MagicMock()

    engine = TradingEngine(
        broker=mock_broker,
        email=mock_email,
        telegram=mock_telegram,
        discord=mock_discord,
        position_manager=mock_pos_mgr,
        config={
            'execution': {},
            'risk_management': {'max_capital_per_trade': 0.02, 'max_portfolio_heat': 0.3,
                                'daily_loss_limit': 0.05, 'max_drawdown': 0.2,
                                'volatility_stop_multiplier': 2.0,
                                'max_net_exposure': 0.5, 'max_gross_exposure': 1.5,
                                'max_position_pct': 0.2},
            'strategies': {'active': [], 'parameters': {}},
            'general': {'bot_name': 'TestBot', 'log_level': 'INFO', 'timezone': 'UTC'},
            'monitoring': {'health_check_port': 8000},
            'exchanges': {'nyse': {'port': 4002, 'client_id': 1, 'account_id': 'DU123'}},
            'data': {'sources': [{'name': 'yahoo', 'enabled': True}], 'update_frequency': {'historical': '1d', 'intraday': '5m'}}
        }
    )
    engine._calculate_commission = lambda quantity, price: 0.0
    engine._simulate_partial_fill = lambda requested_qty: requested_qty
    engine._check_shortable = lambda symbol, quantity: True
    engine._earnings_nearby = lambda symbol: False
    engine._check_net_exposure = lambda action, symbol, quantity, last_price, latest_prices: True

    result = engine._place_trade('AAPL', 'BUY', 100, 150.0, 145.0, 1.0, 2.0)
    assert result is True
    mock_broker.place_bracket_long.assert_called_once()
    mock_email.send_trade_alert.assert_called_once()