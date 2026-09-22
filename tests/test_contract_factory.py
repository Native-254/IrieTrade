#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from execution.ib_broker import IBBroker
from ib_insync import Contract, Forex, Stock, Future

# Create a dummy broker instance (we won't connect)
broker = IBBroker()  # No arguments needed

# Test symbols
test_cases = [
    ("EUR.USD", Forex),
    ("GBP.USD", Forex),
    ("USD.JPY", Forex),
    ("AUD.USD", Forex),
    ("XAUUSD", Stock),  # metals -> Stock
    ("XAGUSD", Stock),
    ("CL", Future),     # energy -> Future
    ("BZ", Future),
    ("GC", Future),
    ("SI", Future),
    ("ES", Future),
    ("NQ", Future),
    ("YM", Future),
    ("RTY", Future),
    ("AAPL", Stock),    # default stock
    ("MSFT", Stock),
]

print("Testing contract factory...")
all_passed = True
for symbol, expected_type in test_cases:
    try:
        contract = broker._make_contract(symbol)
        if isinstance(contract, expected_type):
            print(f"✓ {symbol}: {type(contract).__name__}")
        else:
            print(f"✗ {symbol}: expected {expected_type.__name__}, got {type(contract).__name__}")
            all_passed = False
    except Exception as e:
        print(f"✗ {symbol}: exception {e}")
        all_passed = False

if all_passed:
    print("\nAll contract factory tests passed!")
else:
    print("\nSome contract factory tests failed!")
    sys.exit(1)
