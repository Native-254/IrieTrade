from live.engine import TradingEngine


def test_is_crypto_broker_identifies_non_ib_and_non_nse_brokers():
    engine = object.__new__(TradingEngine)

    assert engine._is_crypto_broker("binance") is True
    assert engine._is_crypto_broker("kucoin") is True
    assert engine._is_crypto_broker("okx") is True
    assert engine._is_crypto_broker("ib") is False
    assert engine._is_crypto_broker("nse") is False
