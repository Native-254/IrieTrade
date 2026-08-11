import asyncio

import monitoring.api as api_module


class _BrokerManagerStub:
    brokers = {"ib": object()}


class _EngineStub:
    risk_managers = {"ib": object()}
    broker_manager = _BrokerManagerStub()
    position_managers = {"ib": object()}

    def _place_trade(self, *_args, **_kwargs):
        raise RuntimeError("secret broker stack detail")


class _RequestStub:
    async def json(self):
        return {
            "symbol": "AAPL",
            "action": "BUY",
            "quantity": 1,
            "price": 100.0,
        }


def test_webhook_exception_response_does_not_expose_details(monkeypatch):
    monkeypatch.setattr(api_module, "trading_engine", _EngineStub())

    response = asyncio.run(api_module.tradingview_webhook(_RequestStub()))

    assert response == {"error": "Internal webhook processing error"}
    assert "secret broker stack detail" not in str(response)
