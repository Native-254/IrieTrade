from types import SimpleNamespace

from execution.ib_broker import IBBroker


def test_round_to_tick_uses_nearest_valid_increment():
    assert IBBroker._round_to_tick(105.75588227135795, 0.01) == 105.76
    assert IBBroker._round_to_tick(408.9575762525286, 0.05) == 408.95


def test_normalize_price_uses_contract_min_tick():
    broker = object.__new__(IBBroker)
    broker.ib = SimpleNamespace(
        reqContractDetails=lambda _contract: [SimpleNamespace(minTick=0.01)]
    )

    assert broker._normalize_price(object(), 70.81494168145316) == 70.81
