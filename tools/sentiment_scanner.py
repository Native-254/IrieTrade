"""Trending crypto scanner using CoinGecko public API (no API key)."""

import requests

from utils.logger import log


class TrendingScanner:
    """Fetches trending coins from CoinGecko."""

    @staticmethod
    def get_trending_coins() -> list[str]:
        """Return a list of trending coin symbols (e.g., 'BTC', 'ETH', 'DOGE')."""
        url = "https://api.coingecko.com/api/v3/search/trending"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            coins = [item["item"]["symbol"].upper() for item in data.get("coins", [])]
            log.info(f"Trending coins fetched: {', '.join(coins[:10])}")
            return coins
        except Exception as e:  # noqa: BLE001
            log.warning(f"Failed to fetch trending coins: {e}")
            return []

    @staticmethod
    def trending_usdt_pairs(exchange_name: str = "kucoin",
                           min_volume_usdt: float = 5_000_000) -> list[str]:
        """Convert trending coin symbols to USDT pairs, filtering by exchange listing and volume."""
        import ccxt

        # Get trending coins from CoinGecko
        symbols = TrendingScanner.get_trending_coins()
        if not symbols:
            return []

        # Load exchange markets
        exchange_class = getattr(ccxt, exchange_name)
        exchange = exchange_class({"enableRateLimit": True})
        exchange.load_markets()

        filtered = []
        for sym in symbols:
            pair = f"{sym}/USDT"
            # Skip if not traded on the exchange
            if pair not in exchange.markets:
                continue
            # Skip if not liquid enough
            ticker = exchange.fetch_ticker(pair)
            quote_vol = ticker.get("quoteVolume") or 0
            if quote_vol < min_volume_usdt:
                continue
            filtered.append(pair)
        return filtered
