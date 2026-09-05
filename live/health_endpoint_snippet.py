import time
from threading import Thread

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel


# Define health response model
class HealthStatus(BaseModel):
    status: str
    ibkr_connected: bool
    data_fresh: bool
    last_error: str | None = None
    uptime_sec: float

# Create a separate FastAPI app for health
health_app = FastAPI()

@health_app.get("/health", response_model=HealthStatus)
def health():
    # TODO: Replace these stubs with actual checks from your engine
    ibkr_connected = check_ibkr_connection()      # implement this function
    data_fresh = is_data_fresh()                  # implement this function
    last_error = get_last_error() if hasattr(globals(), 'get_last_error') else None
    uptime_sec = time.time() - getattr(globals(), 'START_TIME', time.time())
    return HealthStatus(
        status="ok" if ibkr_connected and data_fresh else "degraded",
        ibkr_connected=ibkr_connected,
        data_fresh=data_fresh,
        last_error=last_error,
        uptime_sec=uptime_sec
    )

def run_health_server():
    # Runs on localhost:8000; change port if needed
    uvicorn.run(health_app, host="127.0.0.1", port=8000, log_level="error")

# Start health server in a daemon thread when the engine starts
# Place this near the end of your engine initialization (after setting up brokers, etc.)
Thread(target=run_health_server, daemon=True).start()

# -------------------------------------------------
# Example stub functions you’ll need to implement/adapt:
# -------------------------------------------------
def check_ibkr_connection() -> bool:
    """Return True if IBKR connection is alive."""
    # Example using ib_insync:
    # try:
    #     return ib.isConnected()  # where ib is your IB instance
    # except Exception:
    #     return False
    return True  # stub

def is_data_fresh() -> bool:
    """Return True if market data is updating within expected interval."""
    # Compare last tick timestamp vs now
    return True  # stub

def get_last_error() -> str | None:
    """Return the most recent error message, if any."""
    return None  # stub