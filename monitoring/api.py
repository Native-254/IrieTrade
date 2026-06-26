from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import plotly.graph_objects as go
from utils.config import CONFIG
from utils.logger import log
from execution.ib_broker import IBBroker  # For a quick connectivity check
from typing import Optional, Any

app = FastAPI()
broker = IBBroker()

# Global reference to trading engine (set at runtime)
trading_engine: Optional[Any] = None

def set_trading_engine(engine):
    """Set the global trading engine instance for API access."""
    global trading_engine
    trading_engine = engine
    log.info("Trading engine registered with API")

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "bot_name": CONFIG['general']['bot_name']}

@app.get("/account")
async def account_info():
    """Returns current account information."""
    try:
        broker.connect()
        info = broker.get_account_info()
        broker.disconnect()
        return {"status": "success", "data": info}
    except Exception as e:
        log.error(f"Failed to fetch account info for API: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Interactive dashboard with equity curve and trade history."""
    global trading_engine
    
    # Build equity curve from trade results
    equity_values = [trading_engine.risk_manager.initial_capital if trading_engine else 100000]
    dates = ["Start"]
    
    if trading_engine and trading_engine.trade_results:
        cumulative_pnl = 0.0
        for i, (result_type, pnl_frac) in enumerate(trading_engine.trade_results[-20:]):  # Last 20 trades
            cumulative_pnl += pnl_frac
            current_equity = equity_values[0] * (1 + cumulative_pnl)
            equity_values.append(current_equity)
            dates.append(f"Trade {i+1}")
    
    # Create Plotly figure
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates,
        y=equity_values,
        mode='lines+markers',
        name='Equity',
        line=dict(color='#1f77b4', width=2)
    ))
    fig.update_layout(
        title="Equity Curve",
        xaxis_title="Trade",
        yaxis_title="Account Value ($)",
        hovermode='x unified',
        template='plotly_dark'
    )
    plot_html = fig.to_html(full_html=False)
    
    # Get position and trade info
    num_open_positions = len(trading_engine.open_positions) if trading_engine else 0
    num_trades = len(trading_engine.trade_results) if trading_engine else 0
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mkopo Bot Dashboard</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #0f1419;
                color: #e0e0e0;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            h1 {{
                color: #1f77b4;
                margin-bottom: 10px;
            }}
            .metrics {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .metric-card {{
                background: #1a1e25;
                padding: 20px;
                border-radius: 8px;
                border-left: 4px solid #1f77b4;
            }}
            .metric-value {{
                font-size: 28px;
                font-weight: bold;
                color: #1f77b4;
                margin-top: 10px;
            }}
            .metric-label {{
                font-size: 14px;
                color: #888;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            .chart-container {{
                background: #1a1e25;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 20px;
            }}
            .refresh {{
                text-align: right;
                color: #666;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Mkopo Trading Bot Dashboard</h1>
            <div class="refresh">Auto-refresh every 30s</div>
            
            <div class="metrics">
                <div class="metric-card">
                    <div class="metric-label">Open Positions</div>
                    <div class="metric-value">{num_open_positions}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Closed Trades</div>
                    <div class="metric-value">{num_trades}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Status</div>
                    <div class="metric-value">🟢 Running</div>
                </div>
            </div>
            
            <div class="chart-container">
                <h2>Equity Curve</h2>
                {plot_html}
            </div>
        </div>
        
        <script>
            // Auto-refresh every 30 seconds
            setTimeout(() => location.reload(), 30000);
        </script>
    </body>
    </html>
    """

def run_api():
    """Starts the FastAPI server."""
    port = CONFIG['monitoring']['health_check_port']
    log.info(f"Starting health check API on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
