# tests/test_signal_resolver.py

def resolve(enter_long, exit_long, enter_short, exit_short, current_side):
    if exit_long and current_side == 'BUY':
        return 'SELL'
    elif exit_short and current_side == 'SELL':
        return 'BUY_TO_COVER'
    elif enter_long and current_side is None:
        return 'BUY'
    elif enter_long and current_side == 'SELL':
        return 'BUY_TO_COVER'
    elif enter_short and current_side is None:
        return 'SELL_SHORT'
    elif enter_short and current_side == 'BUY':
        return 'SELL'
    return None

def test_flat_enter_long():
    assert resolve(True, False, False, False, None) == 'BUY'

def test_flat_enter_short():
    assert resolve(False, False, True, False, None) == 'SELL_SHORT'

def test_long_exit_long():
    assert resolve(False, True, False, False, 'BUY') == 'SELL'

def test_short_exit_short():
    assert resolve(False, False, False, True, 'SELL') == 'BUY_TO_COVER'

def test_short_enter_long():
    assert resolve(True, False, False, False, 'SELL') == 'BUY_TO_COVER'

def test_long_enter_short():
    assert resolve(False, False, True, False, 'BUY') == 'SELL'

def test_already_long_enter_long():
    assert resolve(True, False, False, False, 'BUY') is None

def test_already_short_enter_short():
    assert resolve(False, False, True, False, 'SELL') is None