# strategies/base.py
from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, parameters: dict):
        self.name = self.__class__.__name__
        self.parameters = parameters
        self.positions = {}  # Tracks current positions {symbol: size}

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Generates trading signals based on input data."""
        ...

    def calculate_position_size(self, capital: float, risk_per_trade: float, entry_price: float, stop_loss_price: float) -> int:
        """Standard position sizing: (Capital * Risk) / |Entry - Stop|"""
        risk_amount = capital * risk_per_trade
        if entry_price == stop_loss_price:
            return 0
        trade_risk = abs(entry_price - stop_loss_price)
        size = int(risk_amount / trade_risk)
        return max(0, size)

    def __repr__(self):
        return f"{self.name}(parameters={self.parameters})"