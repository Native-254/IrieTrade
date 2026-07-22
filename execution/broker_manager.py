# execution/broker_manager.py
from execution.ib_broker import IBBroker
from execution.binance_broker import BinanceBroker
from execution.okx_broker import OKXBroker
from execution.coinbase_broker import CoinbaseBroker
from execution.kraken_broker import KrakenBroker
from execution.kucoin_broker import KucoinBroker

class BrokerManager:
    def __init__(self, config):
        self.brokers = {}
        self._init_brokers(config)

    def _init_brokers(self, config):
        platforms = config['trading'].get('platforms', [config['trading']['platform']])
        exchanges_cfg = config['exchanges']

        if 'ib' in platforms:
            self.brokers['ib'] = IBBroker()
        if 'binance' in platforms:
            self.brokers['binance'] = BinanceBroker(exchanges_cfg.get('binance', {}))
        if 'okx' in platforms:
            self.brokers['okx'] = OKXBroker(exchanges_cfg.get('okx', {}))
        if 'coinbase' in platforms:
            self.brokers['coinbase'] = CoinbaseBroker(exchanges_cfg.get('coinbase', {}))
        if 'kraken' in platforms:
            self.brokers['kraken'] = KrakenBroker(exchanges_cfg.get('kraken', {}))
        if 'kucoin' in platforms:
            self.brokers['kucoin'] = KucoinBroker(exchanges_cfg.get('kucoin', {}))

        if not self.brokers:
            raise ValueError("No trading platform enabled. Check config.")

    def iterate_all(self):
        for name, broker in self.brokers.items():
            yield name, broker