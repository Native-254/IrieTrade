# test_ib.py
from execution.ib_broker import IBBroker

broker = IBBroker()
broker.connect()
info = broker.get_account_info()
print("Account Info:", info)
broker.disconnect()
