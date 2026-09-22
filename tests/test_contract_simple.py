#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from execution.ib_broker import IBBroker

broker = IBBroker()

symbols = ["EUR.USD", "XAUUSD", "CL", "AAPL"]
for s in symbols:
    c = broker._make_contract(s)
    print(f"{s}: {type(c).__name__}")

print("Done")
