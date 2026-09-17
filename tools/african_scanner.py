"""African market scanner with dual-source data fetching.

Primary:  Mansa Markets API (requires MANSA_API_KEY)
Fallback: afx.kwayisi.org HTML tables (only as last resort)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from utils.logger import log


MANSA_BASE = "https://mansaapi.com/api/v1"
AFX_TEMPLATE = "https://afx.kwayisi.org/{market}/"
AFX_MARKETS = {
    "NSE": "nse",
    "JSE": "jse",
    "NGX": "ngx",
    "GSE": "gse",
    "BRVM": "brvm",
}


class AfricanMarketScanner:
    """Fetches quotes from Mansa pan-African feed, falling back to afx.kwayisi.org."""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.api_key = os.getenv(cfg.get("mansa_api_key_env", "MANSA_API_KEY"), "")
        self.top_n = cfg.get("top_n", 5)
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    # ------------------------------------------------------------------
    # Mansa - Pan-African feed (one call covers all exchanges)
    # ------------------------------------------------------------------
    def _fetch_mansa(self) -> list[dict]:
        """Fetch pan-African movers. One call covers all exchanges."""
        if not self.api_key:
            return []
        try:
            resp = requests.get(
                f"{MANSA_BASE}/markets/movers/pan-african",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            if resp.status_code != 200:
                log.warning(f"Mansa pan-african returned {resp.status_code}")
                return []
            data = resp.json()
        except (ValueError, KeyError, requests.RequestException) as e:
            log.warning(f"Mansa pan-african fetch failed: {e}")
            return []

        payload = data.get("data", {}) if isinstance(data, dict) else {}
        gainers = payload.get("gainers", []) or []
        losers = payload.get("losers", []) or []

        rows = []
        for r in gainers + losers:
            sym = r.get("ticker") or r.get("symbol")
            price = r.get("price")
            chg = r.get("change_pct")
            vol = r.get("volume")
            ex = r.get("exchange") or ""
            if not sym or price is None:
                continue
            rows.append({
                "symbol": str(sym).upper(),
                "price": float(price),
                "change_pct": float(chg) if chg is not None else 0.0,
                "volume": float(vol) if vol is not None else 0.0,
                "exchange": ex.upper(),
                "name": r.get("name", ""),
            })
        return rows

    # ------------------------------------------------------------------
    # afx.kwayisi.org fallback (only used when Mansa returns nothing)
    # ------------------------------------------------------------------
    def _fetch_afx(self, exchange: str) -> list[dict]:
        market = AFX_MARKETS.get(exchange)
        if not market:
            return []
        url = AFX_TEMPLATE.format(market=market)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; IrieTrade/1.0)"},
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning(f"afx {exchange} returned {resp.status_code}")
                return []
            tables = pd.read_html(resp.text)
        except (ValueError, KeyError, requests.RequestException) as e:
            log.warning(f"afx {exchange} scrape failed: {e}")
            return []

        # Find the table with Ticker/Price columns
        df = None
        for t in tables:
            cols = {str(c).strip().lower() for c in t.columns}
            if "ticker" in cols and "price" in cols:
                df = t
                break
        if df is None or df.empty:
            return []

        df.columns = [str(c).strip() for c in df.columns]
        rename = {}
        for c in df.columns:
            lc = c.lower()
            if lc == "ticker" or lc == "symbol":
                rename[c] = "symbol"
            elif lc == "price":
                rename[c] = "price"
            elif lc == "change":
                rename[c] = "change_pct"
            elif lc == "volume":
                rename[c] = "volume"
        df = df.rename(columns=rename)

        if "symbol" not in df.columns or "price" not in df.columns:
            return []

        def _num(x):
            try:
                return float(str(x).replace(",", "").replace("%", "").strip())
            except (ValueError, TypeError):
                return None

        df["price"] = df["price"].apply(_num)
        if "change_pct" in df.columns:
            df["change_pct"] = df["change_pct"].apply(_num)
        else:
            df["change_pct"] = 0.0
        if "volume" in df.columns:
            df["volume"] = df["volume"].apply(_num)
        else:
            df["volume"] = 0.0

        df = df.dropna(subset=["symbol", "price"])
        return df.to_dict(orient="records")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_quotes(self, exchange: str, cache_seconds: int = 300) -> list[dict]:
        """Return quotes for one exchange, from the pan-African movers feed."""
        now = time.time()
        cached = self._cache.get("__pan_african__")
        if cached and (now - cached[0]) < cache_seconds:
            all_rows = cached[1]
        else:
            all_rows = self._fetch_mansa()
            if all_rows:
                self._cache["__pan_african__"] = (now, all_rows)
                log.info(f"Mansa pan-african: {len(all_rows)} movers cached")

        # Filter by exchange locally
        filtered = [r for r in all_rows if r.get("exchange") == exchange.upper()]

        # Optional fallback: try afx only if we have nothing at all from Mansa
        if not filtered and exchange == "NSE":
            try:
                filtered = self._fetch_afx("NSE")
            except (ValueError, KeyError, requests.RequestException):
                pass

        return filtered

    def generate_report(self, exchange: str) -> str:
        quotes = self.get_quotes(exchange)
        today = datetime.now(timezone.utc).strftime("%d %b %Y")

        if not quotes:
            return f"📈 *{exchange} Brief — {today}*\n\nData unavailable. Will retry next cycle."

        ranked = sorted(
            [q for q in quotes if q.get("change_pct") is not None],
            key=lambda q: float(q.get("change_pct") or 0),
            reverse=True,
        )[: self.top_n]

        lines = [f"📈 *{exchange} Brief — {today}*", "", "Top movers:"]
        for q in ranked:
            sym = q.get("symbol", "?")
            price = q.get("price") or 0
            chg = q.get("change_pct") or 0
            vol = q.get("volume") or 0
            lines.append(
                f"• *{sym}* — {price:,.2f} ({chg:+.2f}%)  vol {vol:,.0f}"
            )
        lines.append("")
        lines.append("_Not financial advice._")
        return "\n".join(lines)
