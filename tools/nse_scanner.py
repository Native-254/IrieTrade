
import yfinance as yf


class NSEScanner:
    def __init__(self, config: dict):
        self.config = config.get("nse", {})
        self.universe = self.config.get("universe", [
            "SCOM.NR", "EQTY.NR", "KCB.NR", "EABL.NR", "COOP.NR",
            "ABSA.NR", "NCBA.NR", "KEGN.NR", "KPLC.NR", "SBIC.NR",
            "DTK.NR", "SCBK.NR", "HFCK.NR", "NMG.NR", "JUB.NR",
        ])
        self.top_n = self.config.get("top_n", 5)

    def scan(self) -> list[dict]:
        """Return ranked list of NSE candidates with quote + momentum data."""
        results = []
        for ticker in self.universe:
            try:
                df = yf.download(ticker, period="60d", interval="1d", progress=False)
                if df is None or df.empty or len(df) < 30:
                    continue
                close = df["Close"]
                volume = df["Volume"]
                last = float(close.iloc[-1])
                sma20 = float(close.rolling(20).mean().iloc[-1])
                sma50 = float(close.rolling(50).mean().iloc[-1])
                avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
                last_vol = float(volume.iloc[-1])
                vol_ratio = last_vol / avg_vol_20 if avg_vol_20 > 0 else 0
                chg_5d = (last / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0
                chg_20d = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0

                # Basic momentum score
                score = 0
                if last > sma20: score += 1
                if sma20 > sma50: score += 1
                if chg_5d > 0: score += 1
                if vol_ratio > 1.2: score += 1
                if chg_20d > 3: score += 1

                results.append({
                    "ticker": ticker.replace(".NR", ""),
                    "yahoo": ticker,
                    "price": last,
                    "sma20": sma20,
                    "sma50": sma50,
                    "chg_5d": chg_5d,
                    "chg_20d": chg_20d,
                    "vol_ratio": vol_ratio,
                    "score": score,
                })
            except Exception as e:  # noqa: BLE001
                # Log error but continue scanning other tickers
                print(f"Error scanning {ticker}: {e}")
                continue

        results.sort(key=lambda r: (r["score"], r["chg_5d"]), reverse=True)
        return results[: self.top_n]