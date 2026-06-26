# risk/manager.py
from utils.config import CONFIG
from utils.logger import log

class RiskManager:
    def __init__(self, initial_capital: float):
        self.config = CONFIG['risk_management']
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.open_risk = 0.0 # Total risk across all open positions
        self.daily_pnl = 0.0

    def can_trade(self) -> bool:
        """Checks if trading is allowed based on daily loss limits and drawdown."""
        # 1. Daily Loss Limit Check
        if self.daily_pnl <= -self.current_capital * self.config['daily_loss_limit']:
            log.warning(f"Daily loss limit reached. P&L: {self.daily_pnl:.2f}")
            return False

        # 2. Max Drawdown Check
        current_drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        if current_drawdown >= self.config['max_drawdown']:
            log.warning(f"Max drawdown reached: {current_drawdown*100:.2f}%. Trading halted.")
            return False

        return True

    def validate_order(self, symbol: str, side: str, quantity: int, entry_price: float, stop_price: float) -> tuple[bool, str]:
        """Validates an order against all risk rules before execution."""
        if not self.can_trade():
            return False, "Global risk limits exceeded."

        # 1. Position Sizing Validation
        proposed_risk = quantity * abs(entry_price - stop_price)
        max_allowable_risk = self.current_capital * self.config['max_capital_per_trade']
        if proposed_risk > max_allowable_risk:
            return False, f"Proposed trade risk {proposed_risk:.2f} > max allowable {max_allowable_risk:.2f}"

        # 2. Portfolio Heat Check
        if self.open_risk + proposed_risk > self.current_capital * self.config['max_portfolio_heat']:
            return False, f"Insufficient risk budget. Open risk: {self.open_risk:.2f}, Proposed: {proposed_risk:.2f}"

        log.info(f"Order for {symbol} validated: {side} {quantity} shares.")
        return True, "Order validated."

    def update_portfolio(self, pnl_change: float, open_risk_change: float):
        """Updates the risk manager's view of the portfolio."""
        self.current_capital += pnl_change
        self.daily_pnl += pnl_change
        self.open_risk += open_risk_change
        self.peak_capital = max(self.peak_capital, self.current_capital)
        log.debug(f"Portfolio updated. Capital: {self.current_capital:.2f}, Open Risk: {self.open_risk:.2f}")

    def reset_daily_pnl(self):
        """Call this at the start of a new trading day."""
        log.info(f"Resetting daily P&L. Yesterday's P&L was {self.daily_pnl:.2f}")
        self.daily_pnl = 0.0