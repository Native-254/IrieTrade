"""
Blotter pattern implementation for persistent OHLCV storage.
Uses SQLite to store market data and reduce API calls.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


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
                # Enable WAL mode for better concurrent access
                conn.execute("PRAGMA journal_mode=WAL")

                # Create ohlcv table if not exists
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

                # Create index for faster queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time
                    ON ohlcv(symbol, timestamp DESC)
                """)

                conn.commit()
            finally:
                conn.close()

    def store_ohlcv(self, symbol: str, df: pd.DataFrame, timeframe: str = "1h"):
        """
        Store OHLCV data for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'AAPL', 'BTC/USDT')
            df: DataFrame with OHLCV data (must have columns: open, high, low, close, volume)
            timeframe: Timeframe of the data (e.g., '1m', '5m', '1h', '1d')
        """
        if df.empty:
            return

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                # Prepare data for insertion
                df_to_store = df.copy()
                # Ensure timestamp is in milliseconds since epoch
                if 'timestamp' not in df_to_store.columns:
                    # Assume index is datetime
                    df_to_store['timestamp'] = df_to_store.index.astype('int64') // 10**6

                # Select and rename columns
                df_to_store = df_to_store[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
                df_to_store['symbol'] = symbol
                df_to_store['timeframe'] = timeframe

                # Insert or replace data
                df_to_store.to_sql('ohlcv', conn, if_exists='append', index=False, method='multi')
                conn.commit()
            finally:
                conn.close()

    def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int | None = None) -> pd.DataFrame:
        """
        Retrieve OHLCV data for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe of data to retrieve
            limit: Maximum number of rows to return (most recent first)

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
                if limit:
                    query += f" LIMIT {limit}"

                df = pd.read_sql_query(query, conn, params=(symbol, timeframe))

                if df.empty:
                    return pd.DataFrame()

                # Convert timestamp to datetime and set as index
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                df.set_index('timestamp', inplace=True)
                df.sort_index(inplace=True)  # Sort ascending for consistency

                return df
            finally:
                conn.close()

    def last_updated(self, symbol: str, timeframe: str = "1h") -> datetime | None:
        """
        Get the timestamp of the last update for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe to check

        Returns:
            Datetime of last update or None if no data
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cursor = conn.execute(
                    """
                    SELECT MAX(timestamp)
                    FROM ohlcv
                    WHERE symbol = ? AND timeframe = ?
                    """,
                    (symbol, timeframe)
                )
                result = cursor.fetchone()
                if result and result[0]:
                    # Convert milliseconds to datetime
                    return datetime.fromtimestamp(result[0] / 1000, tz=timezone.utc)
                return None
            finally:
                conn.close()

    def stats(self) -> dict[str, dict]:
        """
        Get statistics about stored data.

        Returns:
            Dictionary with symbol/timeframe as keys and stats as values
        """
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
                        'count': count,
                        'oldest': datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc) if min_ts else None,
                        'newest': datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc) if max_ts else None,
                    }
                return stats
            finally:
                conn.close()

    def clear_old_data(self, days_to_keep: int = 30):
        """
        Clear data older than specified days.

        Args:
            days_to_keep: Number of days of data to retain
        """
        cutoff_ts = int((datetime.now(timezone.utc).timestamp() - (days_to_keep * 86400)) * 1000)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    "DELETE FROM ohlcv WHERE timestamp < ?",
                    (cutoff_ts,)
                )
                conn.commit()
                # Vacuum to reclaim space
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