# risk/manager.py
from utils.config import CONFIG
from utils.logger import log

class RiskManager:
    def __init__(self, initial_capital: float, position_manager=None):
        self.config = CONFIG['risk_management']
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.open_risk = 0.0
        self.daily_pnl = 0.0
        self.position_manager = position_manager

    def can_trade(self) -> bool:
        if self.daily_pnl <= -self.current_capital * self.config['daily_loss_limit']:
            log.warning(f"Daily loss limit reached. P&L: {self.daily_pnl:.2f}")
            return False
        current_drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        if current_drawdown >= self.config['max_drawdown']:
            log.warning(f"Max drawdown reached: {current_drawdown*100:.2f}%. Trading halted.")
            return False
        return True

    def validate_order(self, symbol: str, side: str, quantity: int, entry_price: float, stop_price: float) -> tuple[bool, str]:
        if not self.can_trade():
            return False, "Global risk limits exceeded."

        proposed_risk = quantity * abs(entry_price - stop_price)
        max_allowable_risk = self.current_capital * self.config['max_capital_per_trade']
        if proposed_risk > max_allowable_risk:
            return False, f"Proposed trade risk {proposed_risk:.2f} > max allowable {max_allowable_risk:.2f}"

        if self.open_risk + proposed_risk > self.current_capital * self.config['max_portfolio_heat']:
            return False, f"Insufficient risk budget. Open risk: {self.open_risk:.2f}, Proposed: {proposed_risk:.2f}"

        log.info(f"Order for {symbol} validated: {side} {quantity} shares.")
        return True, "Order validated."

    def recalc_open_risk(self, latest_prices: dict):
        """Recalculate total open risk from position manager using latest prices."""
        if not self.position_manager:
            return
        total_risk = 0.0
        for pos in self.position_manager.positions.values():
            if pos.stop_loss and pos.symbol in latest_prices:
                price = latest_prices[pos.symbol]
                total_risk += pos.quantity * abs(price - pos.stop_loss)
        self.open_risk = total_risk
        log.debug(f"Recalculated open risk: {self.open_risk:.2f}")

    def get_gross_exposure(self, latest_prices: dict) -> float:
        """Return total notional of all positions."""
        if not self.position_manager:
            return 0.0
        gross = 0.0
        for pos in self.position_manager.positions.values():
            price = latest_prices.get(pos.symbol, pos.entry_price)
            gross += pos.quantity * price
        log.debug(f"Gross exposure computed: {gross:.2f}")
        return gross

    def get_position_notional(self, symbol: str, price: float) -> float:
        """Return notional of a single position."""
        if not self.position_manager:
            return 0.0
        pos = self.position_manager.positions.get(symbol)
        if pos:
            notional = pos.quantity * price
            log.debug(f"Position notional for {symbol}: {notional:.2f}")
            return notional
        return 0.0

    def get_net_exposure(self, latest_prices: dict) -> float:
        """Return long_notional - short_notional, using latest market prices."""
        if not self.position_manager:
            return 0.0
        long_exp = 0.0
        short_exp = 0.0
        for pos in self.position_manager.positions.values():
            price = latest_prices.get(pos.symbol, pos.entry_price)
            notional = pos.quantity * price
            if pos.side == 'BUY':
                long_exp += notional
            else:
                short_exp += notional
        net = long_exp - short_exp
        log.debug(f"Net exposure computed: {net:.2f} (long {long_exp:.2f}, short {short_exp:.2f})")
        return net

    def update_portfolio(self, pnl_change: float, open_risk_change: float):
        self.current_capital += pnl_change
        self.daily_pnl += pnl_change
        self.open_risk += open_risk_change
        self.peak_capital = max(self.peak_capital, self.current_capital)
        log.debug(f"Portfolio updated. Capital: {self.current_capital:.2f}, Open Risk: {self.open_risk:.2f}")

    def reset_daily_pnl(self):
        log.info(f"Resetting daily P&L. Yesterday's P&L was {self.daily_pnl:.2f}")
        self.daily_pnl = 0.0