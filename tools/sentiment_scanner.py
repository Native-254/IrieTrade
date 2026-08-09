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
        except Exception as e:
            log.warning(f"Failed to fetch trending coins: {e}")
            return []

    @staticmethod
    def trending_usdt_pairs() -> list[str]:
        """Convert trending coin symbols to USDT pairs (for KuCoin)."""
        symbols = TrendingScanner.get_trending_coins()
        return [f"{sym}/USDT" for sym in symbols if sym not in ("USDT", "USDC", "BUSD", "TUSD")]
