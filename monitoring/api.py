# monitoring/api.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import uvicorn
from utils.config import CONFIG
from utils.logger import log

app = FastAPI()

# Global reference (set from live/engine.py)
trading_engine = None

def set_trading_engine(engine):
    global trading_engine
    trading_engine = engine
    log.info("Trading engine registered with API")

@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_name": CONFIG['general']['bot_name']}

@app.get("/account")
async def account_info():
    if not trading_engine:
        return {"status": "error", "message": "Engine not running"}
    try:
        broker = trading_engine.broker
        broker.connect()
        info = broker.get_account_info()
        broker.disconnect()
        return {"status": "success", "data": info}
    except Exception:
        return {"status": "error", "message": "Internal error"}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    if not trading_engine:
        return HTMLResponse("<h2>Bot not started yet.</h2>")

    history = trading_engine.equity_history
    if not history or len(history) < 2:
        return HTMLResponse("<h2>Waiting for data... (first point recorded)</h2>")

    df = pd.DataFrame(history, columns=['time', 'nav'])
    df.set_index('time', inplace=True)
    df = df.sort_index()

    # Summary stats
    last_nav = df['nav'].iloc[-1]
    first_nav = df['nav'].iloc[0]
    total_return = (last_nav - first_nav) / first_nav * 100
    daily_change = df['nav'].iloc[-1] - df['nav'].iloc[-2] if len(df) > 1 else 0
    daily_pct = (daily_change / df['nav'].iloc[-2]) * 100 if len(df) > 1 else 0

    # Plotly chart
    fig = make_subplots(rows=1, cols=1)

    # Color based on trend
    color_line = '#00b894' if total_return >= 0 else '#e17055'

    fig.add_trace(go.Scatter(
        x=df.index,
        y=df['nav'],
        mode='lines',
        line=dict(color=color_line, width=2),
        fill='tozeroy',
        fillcolor='rgba(0, 184, 148, 0.1)' if total_return >= 0 else 'rgba(225, 112, 85, 0.1)',
        name='NAV'
    ))

    fig.update_layout(
        template='plotly_dark',
        title={
            'text': 'Equity Curve',
            'x': 0.1,
            'font': {'size': 24, 'family': 'Arial Black'}
        },
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(
            title='Net Asset Value (USD)',
            showgrid=True,
            gridcolor='rgba(255,255,255,0.05)',
            zeroline=False
        ),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#dfe6e9'),
        margin=dict(l=20, r=20, t=60, b=20),
        hovermode='x unified'
    )

    plot_html = fig.to_html(full_html=False, config={'responsive': True})

    # Number of open positions
    open_pos = len(trading_engine.open_positions)

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Mkopo Trading Bot – Live Dashboard</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background: #0b111a;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                color: #dfe6e9;
            }}
            .header {{
                background: linear-gradient(135deg, #0b111a 0%, #1e1e2f 100%);
                padding: 20px 40px;
                border-bottom: 1px solid #2d3436;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .header h1 {{
                font-weight: 700;
                font-size: 28px;
                color: #00cec9;
            }}
            .stats {{
                display: flex;
                gap: 30px;
                padding: 20px 40px;
                background: #101624;
                flex-wrap: wrap;
            }}
            .stat-box {{
                background: #141d2e;
                padding: 15px 25px;
                border-radius: 8px;
                border-left: 4px solid #00cec9;
                min-width: 150px;
            }}
            .stat-label {{
                font-size: 12px;
                text-transform: uppercase;
                color: #b2bec3;
                letter-spacing: 1px;
            }}
            .stat-value {{
                font-size: 24px;
                font-weight: bold;
                margin-top: 5px;
            }}
            .positive {{ color: #00b894; }}
            .negative {{ color: #e17055; }}
            .chart-container {{
                padding: 20px;
                background: #0b111a;
            }}
            .footer {{
                text-align: center;
                padding: 10px;
                color: #636e72;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📈 Mkopo Trading Bot</h1>
            <div>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        </div>
        <div class="stats">
            <div class="stat-box">
                <div class="stat-label">Net Asset Value</div>
                <div class="stat-value">${last_nav:,.2f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Daily Change</div>
                <div class="stat-value {('positive' if daily_change >= 0 else 'negative')}">
                    {daily_change:+,.2f} ({daily_pct:+.2f}%)
                </div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Total Return</div>
                <div class="stat-value {('positive' if total_return >= 0 else 'negative')}">
                    {total_return:+.2f}%
                </div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Open Positions</div>
                <div class="stat-value">{open_pos}</div>
            </div>
        </div>
        <div class="chart-container">
            {plot_html}
        </div>
        <div class="footer">
            © 2026 Mkopo Trading Bot • Data updates every hour
        </div>
    </body>
    </html>
    """)

def run_api():
    port = CONFIG['monitoring']['health_check_port']
    log.info(f"Starting health check API on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")