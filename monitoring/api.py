# monitoring/api.py
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta
import uvicorn
import yaml
import secrets
import os
from pathlib import Path
from utils.config import CONFIG
from utils.logger import log
from utils.security import encrypt 
from execution.ib_broker import IBBroker
from execution.binance_broker import BinanceBroker
from execution.okx_broker import OKXBroker

app = FastAPI()
security = HTTPBasic()

MASTER_PASSWORD = os.getenv('MASTER_PASSWORD', '')

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct = secrets.compare_digest(credentials.password, MASTER_PASSWORD)
    if not correct:
        raise HTTPException(status_code=401, detail="Incorrect password")
    return True

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
        for broker_name, broker in trading_engine.broker_manager.iterate_all():
            broker.connect()
            info = broker.get_account_info()
            broker.disconnect()
            return {"status": "success", "data": info}
    except Exception:
        return {"status": "error", "message": "Internal error"}

@app.get("/api/signals")
async def api_signals(symbol: str = Query(..., description="Ticker symbol")):
    if not trading_engine:
        return {"error": "Engine not running"}
    df = trading_engine.data_manager.get_data(
        symbol,
        start_date=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),
        end_date=datetime.now().strftime('%Y-%m-%d'),
        interval="15m", force_refresh=True)
    if df.empty:
        return {"error": "No data"}
    signals = [str(strat.generate_signals(df).iloc[-1]) for strat in trading_engine.strategies]
    return {"symbol": symbol, "signals": signals, "last_price": float(df['close'].iloc[-1])}

@app.get("/api/positions")
async def api_positions():
    if not trading_engine:
        return {"error": "Engine not running"}
    positions = []
    for pm in trading_engine.position_managers.values():
        for pos in pm.positions.values():
            positions.append({
                'symbol': pos.symbol,
                'side': pos.side,
                'quantity': pos.quantity,
                'entry_price': pos.entry_price,
                'stop_loss': pos.stop_loss,
            })
    return {"positions": positions}

@app.get("/api/performance")
async def api_performance():
    if not trading_engine:
        return {"error": "Engine not running"}
    nav = sum(rm.current_capital for rm in trading_engine.risk_managers.values())
    daily_pnl = sum(rm.daily_pnl for rm in trading_engine.risk_managers.values())
    open_risk_val = sum(rm.open_risk for rm in trading_engine.risk_managers.values())
    open_positions = sum(len(pm.positions) for pm in trading_engine.position_managers.values())
    return {"nav": nav, "daily_pnl": daily_pnl, "open_risk": open_risk_val, "open_positions": open_positions}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    if not trading_engine:
        return HTMLResponse("<h2>Bot not started yet.</h2>")

    history = trading_engine.equity_history
    if not history or len(history) < 2:
        return HTMLResponse("<h2>Waiting for data... (first point recorded)</h2>")

    nav = sum(rm.current_capital for rm in trading_engine.risk_managers.values())
    daily_pnl = sum(rm.daily_pnl for rm in trading_engine.risk_managers.values())
    open_risk_val = sum(rm.open_risk for rm in trading_engine.risk_managers.values())
    positions = {}
    for pm in trading_engine.position_managers.values():
        positions.update(pm.positions)
    open_pos = len(positions)

    df = pd.DataFrame(history, columns=['time', 'nav'])
    df.set_index('time', inplace=True)
    df = df.sort_index()

    last_nav = df['nav'].iloc[-1] if len(df) else nav
    first_nav = df['nav'].iloc[0] if len(df) else nav
    total_return = (last_nav - first_nav) / first_nav * 100 if first_nav else 0
    daily_change = df['nav'].iloc[-1] - df['nav'].iloc[-2] if len(df) > 1 else 0
    daily_pct = (daily_change / df['nav'].iloc[-2]) * 100 if len(df) > 1 else 0
    current_capital = nav

    latest_prices = {}
    for bp in trading_engine.broker_latest_prices.values():
        latest_prices.update(bp)
    computed_unrealized = 0.0
    for pos in positions.values():
        price = latest_prices.get(pos.symbol, pos.entry_price)
        if pos.side == 'BUY':
            computed_unrealized += (price - pos.entry_price) * pos.quantity
        else:
            computed_unrealized += (pos.entry_price - price) * pos.quantity
    unrealized_pnl = computed_unrealized
    realized_pnl = getattr(trading_engine, 'realized_pnl', 0.0)
    trading_total = unrealized_pnl + realized_pnl
    interest_effect = (last_nav - first_nav) - trading_total

    rolling_sharpe = None
    if len(df) >= 30:
        daily_returns = df['nav'].pct_change().dropna()
        if len(daily_returns) >= 30:
            sharpe_series = (daily_returns.rolling(30).mean() / daily_returns.rolling(30).std()) * (252 ** 0.5)
            rolling_sharpe = sharpe_series.iloc[-1] if not sharpe_series.empty else None
    sharpe_display = f"{rolling_sharpe:.2f}" if rolling_sharpe is not None else '—'

    bot_status = "Running" if trading_engine.is_running else "Stopped"
    portfolio_heat = open_risk_val / current_capital * 100 if current_capital else 0.0

    # Plotly chart
    fig = make_subplots(rows=1, cols=1)
    color_line = '#00b894' if total_return >= 0 else '#e17055'
    fig.add_trace(go.Scatter(
        x=df.index, y=df['nav'], mode='lines',
        line=dict(color=color_line, width=2),
        fill='tozeroy',
        fillcolor='rgba(0, 184, 148, 0.1)' if total_return >= 0 else 'rgba(225, 112, 85, 0.1)',
        name='NAV'
    ))
    fig.update_layout(
        template='plotly_dark',
        title={'text': 'IrieTrade Equity Curve', 'x': 0.05, 'font': {'size': 24, 'family': 'Arial Black'}},
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(title='Net Asset Value (USD)', showgrid=True, gridcolor='rgba(255,255,255,0.05)', zeroline=False),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#dfe6e9'), margin=dict(l=20, r=20, t=60, b=20), hovermode='x unified'
    )
    plot_html = fig.to_html(full_html=False, config={'responsive': True})

    # Positions table
    position_rows = ""
    for pos in positions.values():
        current_price = latest_prices.get(pos.symbol, pos.entry_price)
        if pos.side == 'BUY':
            pnl = (current_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - current_price) * pos.quantity
        pnl_class = 'metric-positive' if pnl >= 0 else 'metric-negative'
        stop_display = f"${pos.stop_loss:,.2f}" if pos.stop_loss else "—"
        position_rows += f"""
            <tr>
                <td>{pos.symbol}</td><td>{pos.side}</td><td>{pos.quantity}</td>
                <td>${pos.entry_price:,.2f}</td><td>{stop_display}</td>
                <td>${current_price:,.2f}</td><td class="{pnl_class}">{pnl:+,.2f}</td>
            </tr>"""
    if not position_rows:
        position_rows = "<tr><td colspan='7'>No active positions</td></tr>"

    recent_trades_html = ""
    if trading_engine.trade_results:
        recent_trades_html = "<ul style='list-style:none; padding:0; color:#b0becd; font-size:14px;'>"
        for result_type, pnl_frac in trading_engine.trade_results[-10:]:
            color = "#00d084" if result_type == 'win' else "#ff7c7c"
            recent_trades_html += f"<li style='margin:4px 0;'><span style='color:{color};'>{result_type.upper()}</span> - {pnl_frac:+.2%}</li>"
        recent_trades_html += "</ul>"
    else:
        recent_trades_html = "<p>No closed trades yet.</p>"

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="60">
        <title>Irie Trade – Live Dashboard</title>
        <style>
            :root {{
                color-scheme: dark;
                font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: #080c14;
                color: #e9edf5;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                min-height: 100vh;
            }}
            .layout {{ display: grid; grid-template-columns: 260px 1fr; gap: 24px; padding: 24px; background: radial-gradient(circle at top left, rgba(0, 184, 148, 0.12), transparent 28%), radial-gradient(circle at bottom right, rgba(255, 121, 198, 0.08), transparent 30%), #080c14; }}
            .sidebar {{ background: rgba(12, 18, 31, 0.95); border: 1px solid rgba(255,255,255,0.06); border-radius: 28px; padding: 28px; display: flex; flex-direction: column; gap: 28px; }}
            .brand {{ display: flex; align-items: center; gap: 14px; }}
            .brand-dot {{ width: 14px; height: 14px; border-radius: 50%; background: linear-gradient(135deg, #00e6b8, #00a0ff); }}
            .brand-title {{ font-size: 24px; font-weight: 800; letter-spacing: -0.02em; color: #ffffff; }}
            .nav-item {{ display: block; padding: 14px 16px; border-radius: 18px; color: #b0becd; text-decoration: none; transition: all 0.2s ease; }}
            .nav-item.active, .nav-item:hover {{ background: rgba(255,255,255,0.06); color: #ffffff; }}
            .panel {{ background: rgba(13, 21, 37, 0.95); border: 1px solid rgba(255,255,255,0.06); border-radius: 28px; padding: 24px; backdrop-filter: blur(18px); }}
            .panel-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 20px; }}
            .panel-header h1 {{ margin: 0; font-size: 32px; letter-spacing: -0.04em; }}
            .panel-header p {{ margin: 6px 0 0; color: #94a3b8; }}
            .grid-cols-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
            .stat-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin-top: 18px; }}
            .stat-card {{ background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01)); border: 1px solid rgba(255,255,255,0.06); border-radius: 24px; padding: 20px; }}
            .stat-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.14em; color: #7b8a99; margin-bottom: 10px; }}
            .stat-value {{ font-size: 28px; font-weight: 700; line-height: 1.1; }}
            .stat-subtext {{ margin-top: 8px; color: #7b8a99; font-size: 13px; }}
            .metric-positive {{ color: #00d084; }}
            .metric-negative {{ color: #ff7c7c; }}
            .asset-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; margin-top: 18px; }}
            .asset-card {{ background: rgba(6, 12, 24, 0.92); border: 1px solid rgba(255,255,255,0.05); border-radius: 24px; padding: 20px; }}
            .asset-card h3 {{ margin: 0; font-size: 18px; }}
            .asset-value {{ font-size: 22px; font-weight: 700; margin-top: 12px; }}
            .asset-change {{ margin-top: 8px; color: #7b8a99; }}
            .positions-table {{ width: 100%; border-collapse: collapse; margin-top: 14px; }}
            .positions-table th, .positions-table td {{ padding: 14px 12px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 14px; }}
            .positions-table th {{ color: #94a3b8; font-weight: 600; }}
            .positions-empty td {{ color: #7b8a99; text-align: center; }}
            .chart-card {{ min-height: 420px; }}
            .footer {{ margin-top: 24px; text-align: center; color: #5f788f; font-size: 13px; }}
            @media (max-width: 1100px) {{ .layout {{ grid-template-columns: 1fr; }} .stat-grid, .asset-grid {{ grid-template-columns: 1fr; }} }}
        </style>
    </head>
    <body>
        <div class="layout">
            <aside class="sidebar">
                <div class="brand">
                    <div class="brand-dot"></div>
                    <div class="brand-title">IrieTrade</div>
                </div>
                <a class="nav-item active" href="#overview">Live Dashboard</a>
                <a class="nav-item" href="#positions">Portfolio</a>
                <a class="nav-item" href="#alerts">Alerts</a>
                <a class="nav-item" href="/setup">Settings</a>
                <a class="nav-item" href="#support">Support</a>
            </aside>
            <main>
                <section id="overview" class="panel">
                    <div class="panel-header">
                        <div>
                            <h1>Live portfolio overview</h1>
                            <p>Monitor NAV, risk, positions, and bot health without interrupting execution.</p>
                        </div>
                        <div>
                            <div class="stat-label">Status</div>
                            <div class="stat-value">{bot_status}</div>
                        </div>
                    </div>
                    <div class="stat-grid">
                        <div class="stat-card">
                            <div class="stat-label">Net Asset Value</div>
                            <div class="stat-value">${last_nav:,.2f}</div>
                            <div class="stat-subtext">Current portfolio value</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Daily P&L</div>
                            <div class="stat-value {('metric-positive' if daily_pnl >= 0 else 'metric-negative')}">{daily_pnl:+,.2f}</div>
                            <div class="stat-subtext">{daily_pct:+.2f}% change</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Unrealised P&L</div>
                            <div class="stat-value {('metric-positive' if unrealized_pnl >= 0 else 'metric-negative')}">{unrealized_pnl:+,.2f}</div>
                            <div class="stat-subtext">Mark‑to‑market</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Trading P&L (U+R)</div>
                            <div class="stat-value {('metric-positive' if trading_total >= 0 else 'metric-negative')}">{trading_total:+,.2f}</div>
                            <div class="stat-subtext">Excludes interest & dividends</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Interest & Div Effect</div>
                            <div class="stat-value {('metric-positive' if interest_effect >= 0 else 'metric-negative')}">{interest_effect:+,.2f}</div>
                            <div class="stat-subtext">NAV change driven by cash</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">30-Day Rolling Sharpe</div>
                            <div class="stat-value {('metric-positive' if rolling_sharpe is not None and rolling_sharpe >= 0 else 'metric-negative')}">{sharpe_display}</div>
                            <div class="stat-subtext">Risk-adjusted return (annualised)</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Portfolio Heat</div>
                            <div class="stat-value">{portfolio_heat:.1f}%</div>
                            <div class="stat-subtext">Open risk vs capital</div>
                        </div>
                    </div>
                    <div class="asset-grid">
                        <div class="asset-card">
                            <h3>Equity trend</h3>
                            <div class="asset-value">{total_return:+.2f}%</div>
                            <div class="asset-change">Total return from first tracked NAV point</div>
                        </div>
                        <div class="asset-card">
                            <h3>Unrealized P&L</h3>
                            <div class="asset-value {('metric-positive' if unrealized_pnl >= 0 else 'metric-negative')}">{unrealized_pnl:+,.2f}</div>
                            <div class="asset-change">Broker unrealized P&L</div>
                        </div>
                        <div class="asset-card">
                            <h3>Positions</h3>
                            <div class="asset-value">{open_pos}</div>
                            <div class="asset-change">Active position count</div>
                        </div>
                        <div class="asset-card">
                            <h3>Latest refresh</h3>
                            <div class="asset-value">{datetime.now().strftime('%H:%M:%S')}</div>
                            <div class="asset-change">Real-time dashboard snapshot</div>
                        </div>
                    </div>
                </section>

                <section class="panel chart-card">
                    {plot_html}
                </section>

                <section id="positions" class="panel">
                    <div class="panel-header">
                        <div>
                            <h1>Open Positions</h1>
                            <p>A snapshot of current portfolio exposure.</p>
                        </div>
                    </div>
                    <div style="overflow-x:auto;">
                        <table class="positions-table">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Side</th>
                                    <th>Qty</th>
                                    <th>Entry</th>
                                    <th>Stop</th>
                                    <th>Current</th>
                                    <th>U-P&L</th>
                                </tr>
                            </thead>
                            <tbody>
                                {position_rows}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section class="panel">
                    <div class="panel-header">
                        <div>
                            <h1>Recent Closed Trades</h1>
                            <p>Last 10 outcomes (win/loss with return).</p>
                        </div>
                    </div>
                    {recent_trades_html}
                </section>

                <div class="footer">
                    © 2026 Irie Trade • Data updates every hour • Dashboard is read-only and does not impact bot execution.
                </div>
            </main>
        </div>
    </body>
    </html>
    """)

# ---------- Onboarding / Setup ----------
def test_broker_connection(broker_name: str, credentials: dict) -> bool:
    """Quickly test if a broker can connect with the given credentials."""
    try:
        if broker_name == 'ib':
            broker = IBBroker()
            broker.config['account_id'] = credentials['account_id']
            broker.connect()
        elif broker_name == 'binance':
            broker = BinanceBroker({'testnet': False})
            broker.api_key = credentials['api_key']
            broker.secret = credentials['secret']
            broker.connect()
        elif broker_name == 'okx':
            broker = OKXBroker({'testnet': False})
            broker.api_key = credentials['api_key']
            broker.secret = credentials['secret']
            broker.password = credentials.get('passphrase', '')
            broker.connect()
        else:
            return False
        broker.get_account_info()  # verify connection works
        broker.disconnect()
        return True
    except Exception:
        return False

@app.post("/api/setup/validate")
async def setup_validate(request: Request, authenticated: bool = Depends(verify_admin)):
    data = await request.json()
    brokers = data.get('brokers', [])
    credentials = data.get('credentials', {})

    for broker in brokers:
        if not test_broker_connection(broker, credentials.get(broker, {})):
            return {"success": False, "message": f"Connection failed for {broker}"}
    return {"success": True, "message": "All connections successful"}

@app.post("/api/setup/save")
async def setup_save(request: Request, authenticated: bool = Depends(verify_admin)):
    data = await request.json()
    brokers = data.get('brokers', [])
    credentials = data.get('credentials', {})
    symbols = data.get('symbols', [])

    # 1. Write .env file
    env_path = Path('.env')
    with open(env_path, 'a') as f:
        for broker in brokers:
            if broker == 'ib':
                val = credentials[broker]['account_id']
                f.write(f"IB_ACCOUNT_ID={encrypt(val)}\n")
            elif broker == 'binance':
                f.write(f"BINANCE_API_KEY={credentials[broker]['api_key']}\n")
                f.write(f"BINANCE_SECRET={credentials[broker]['secret']}\n")
            elif broker == 'okx':
                f.write(f"OKX_API_KEY={credentials[broker]['api_key']}\n")
                f.write(f"OKX_SECRET={credentials[broker]['secret']}\n")
                f.write(f"OKX_PASSPHRASE={credentials[broker].get('passphrase', '')}\n")

    # 2. Update settings.yaml with brokers and symbols
    config_path = Path('config/settings.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    config['trading']['platforms'] = brokers
    config['trading']['platform'] = brokers[0]
    config['trading']['symbols'] = symbols
    with open(config_path, 'w') as f:
        yaml.dump(config, f)

    # 3. Signal the engine to restart with new config
    if trading_engine:
        trading_engine.restart_with_new_config(config)

    return {"success": True, "message": "Configuration saved and bot restarted"}

@app.get("/setup")
async def setup_page():
    return FileResponse("config/setup.html")

def run_api():
    port = CONFIG['monitoring']['health_check_port']
    log.info(f"Starting health check API on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")