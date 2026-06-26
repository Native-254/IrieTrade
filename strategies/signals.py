from enum import Enum

class Signal(Enum):
    ENTER_LONG = 'ENTER_LONG'
    ENTER_SHORT = 'ENTER_SHORT'
    EXIT_LONG = 'EXIT_LONG'
    EXIT_SHORT = 'EXIT_SHORT'
    HOLD = 'HOLD'