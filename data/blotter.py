"""
Blotter pattern implementation for persistent OHLCV storage.
Uses SQLite to store market data and reduce API calls.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from utils.logger import log


class Blotter:
    """
    SQLite-backed OHLCV store with thread-safe reads/writes.
    Implements the Blotter pattern for persistent market data storage.
    """

    def __init__(self, db_path: str = "data/market.db"):
        """
        Initialize the blotter with SQLite database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        """Initialize database tables if they don't exist."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv (
                        symbol TEXT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        timeframe TEXT NOT NULL,
                        PRIMARY KEY (symbol, timestamp, timeframe)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time
                    ON ohlcv(symbol, timestamp DESC)
                """)
                conn.commit()
            finally:
                conn.close()

    def store_ohlcv(self, symbol: str, df: pd.DataFrame, timeframe: str = "1h") -> int:
        """
        Store OHLCV data for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'AAPL', 'BTC/USDT')
            df: DataFrame with OHLCV data
            timeframe: Timeframe of the data (e.g., '1m', '5m', '1h', '1d')

        Returns:
            Number of rows written
        """
        if df is None or df.empty:
            return 0

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                df_to_store = df.copy()

                # Ensure timestamp column exists in milliseconds since epoch
                if "timestamp" not in df_to_store.columns:
                    if df_to_store.index.tz is None:
                        timestamps = df_to_store.index.tz_localize("UTC")
                    else:
                        timestamps = df_to_store.index.tz_convert("UTC")
                    df_to_store["timestamp"] = timestamps.astype("int64") // 10**6

                # Select and add metadata columns
                df_to_store = df_to_store[
                    ["timestamp", "open", "high", "low", "close", "volume"]
                ].copy()
                df_to_store["symbol"] = symbol
                df_to_store["timeframe"] = timeframe

                rows = df_to_store.to_dict(orient="records")

                conn.executemany(
                    """
                    INSERT OR REPLACE INTO ohlcv
                        (symbol, timestamp, open, high, low, close, volume, timeframe)
                    VALUES (:symbol, :timestamp, :open, :high, :low, :close, :volume, :timeframe)
                    """,
                    rows,
                )
                conn.commit()
                return len(rows)
            except Exception as e:  # noqa: BLE001
                log.error(f"Blotter store_ohlcv failed for {symbol}: {e}")
                return 0
            finally:
                conn.close()

    def store_quote_snapshot(
        self,
        symbol: str,
        exchange: str,
        price: float,
        volume: float = 0.0,
        change_pct: float = 0.0,
        timeframe: str = "1d",
    ) -> int:
        """
        Store a single daily snapshot (used for African markets via Mansa).

        Uses the current UTC date's midnight as the bar timestamp so each
        trading day produces exactly one row per symbol per exchange.
        The exchange is prefixed to the symbol to avoid collisions.
        """
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ts_ms = int(today.timestamp() * 1000)
        full_symbol = f"{exchange}:{symbol}"

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ohlcv
                        (symbol, timestamp, open, high, low, close, volume, timeframe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        full_symbol,
                        ts_ms,
                        float(price),
                        float(price),
                        float(price),
                        float(price),
                        float(volume),
                        timeframe,
                    ),
                )
                conn.commit()
                return 1
            except Exception as e:  # noqa: BLE001
                log.error(f"Blotter store_quote_snapshot failed for {full_symbol}: {e}")
                return 0
            finally:
                conn.close()

    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int | None = None
    ) -> pd.DataFrame:
        """
        Retrieve OHLCV data for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe of data to retrieve
            limit: Maximum number of rows to return

        Returns:
            DataFrame with OHLCV data indexed by timestamp
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                query = """
                    SELECT timestamp, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp DESC
                """
                params: tuple = (symbol, timeframe)
                if limit:
                    query += " LIMIT ?"
                    params = (symbol, timeframe, limit)

                df = pd.read_sql_query(query, conn, params=params)

                if df.empty:
                    return pd.DataFrame()

                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)

                return df
            finally:
                conn.close()

    def last_updated(self, symbol: str, timeframe: str = "1h") -> datetime | None:
        """Get the timestamp of the last update for a symbol."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.execute(
                    """
                    SELECT MAX(timestamp)
                    FROM ohlcv
                    WHERE symbol = ? AND timeframe = ?
                    """,
                    (symbol, timeframe),
                )
                result = cursor.fetchone()
                if result and result[0]:
                    return datetime.fromtimestamp(result[0] / 1000, tz=timezone.utc)
                return None
            finally:
                conn.close()

    def stats(self) -> dict[str, dict]:
        """Get statistics about stored data."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.execute("""
                    SELECT symbol, timeframe, COUNT(*) as count,
                           MIN(timestamp) as min_ts, MAX(timestamp) as max_ts
                    FROM ohlcv
                    GROUP BY symbol, timeframe
                """)

                stats = {}
                for row in cursor.fetchall():
                    symbol, timeframe, count, min_ts, max_ts = row
                    key = f"{symbol}:{timeframe}"
                    stats[key] = {
                        "count": count,
                        "oldest": datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc) if min_ts else None,
                        "newest": datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc) if max_ts else None,
                    }
                return stats
            finally:
                conn.close()

    def total_bars(self) -> int:
        """Return the total number of bars stored."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM ohlcv")
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            finally:
                conn.close()

    def clear_old_data(self, days_to_keep: int = 30):
        """Clear data older than specified days."""
        cutoff_ts = int(
            (datetime.now(timezone.utc).timestamp() - (days_to_keep * 86400)) * 1000
        )

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("DELETE FROM ohlcv WHERE timestamp < ?", (cutoff_ts,))
                conn.commit()
                conn.execute("VACUUM")
            finally:
                conn.close()


# Global blotter instance
_blotter_instance: Blotter | None = None


def get_blotter() -> Blotter:
    """Get or create the global blotter instance."""
    global _blotter_instance
    if _blotter_instance is None:
        _blotter_instance = Blotter()
    return _blotter_instance