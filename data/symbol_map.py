"""Translate between broker-native symbols and Yahoo Finance symbols.

IBKR uses dot-separated forex (EUR.USD) and CFD-style tickers (XAUUSD).
Yahoo uses suffixed conventions (EURUSD=X, GC=F). This module maps one to
the other without requiring callers to know Yahoo's quirks.
"""

from __future__ import annotations

YAHOO_MAP: dict[str, str] = {
    # Forex (IBKR dot format → Yahoo)
    "EUR.USD": "EURUSD=X",
    "GBP.USD": "GBPUSD=X",
    "USD.JPY": "JPY=X",
    "USD.CHF": "CHF=X",
    "USD.CAD": "CAD=X",
    "AUD.USD": "AUDUSD=X",
    "NZD.USD": "NZDUSD=X",
    "EUR.GBP": "EURGBP=X",
    "EUR.JPY": "EURJPY=X",
    # Metals (IBKR CFD ticker → Yahoo futures proxy)
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "XPTUSD": "PL=F",
    "XPDUSD": "PA=F",
    # Oil
    "USOIL": "CL=F",
    "UKOIL": "BZ=F",
    # Already-Yahoo futures (identity, kept for clarity)
    "CL=F": "CL=F",
    "BZ=F": "BZ=F",
    "GC=F": "GC=F",
    "SI=F": "SI=F",
}


def to_yahoo_symbol(symbol: str) -> str:
    """Return the Yahoo Finance equivalent of a broker symbol.

    Falls through to the original symbol if no mapping exists, so stocks
    like AAPL and crypto like BTC/USDT pass through unchanged.
    """
    return YAHOO_MAP.get(symbol.upper(), symbol)


def is_mapped_derivative(symbol: str) -> bool:
    """True if the symbol is a forex pair, metal, or oil proxy on Yahoo."""
    return symbol.upper() in YAHOO_MAP