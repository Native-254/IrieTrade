# monitoring/api.py
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import pandas as pd
import plotly.graph_objects as go
import uvicorn
import yaml
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from plotly.subplots import make_subplots

from execution.binance_broker import BinanceBroker
from execution.coinbase_broker import CoinbaseBroker
from execution.ib_broker import IBBroker
from execution.kraken_broker import KrakenBroker
from execution.kucoin_broker import KucoinBroker
from execution.okx_broker import OKXBroker
from utils.config import CONFIG
from utils.logger import log
from utils.security import encrypt

app = FastAPI()

trading_engine = None


def _post_ai_request(api_url: str, api_key: str, payload: dict) -> dict:
    request = UrlRequest(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _dashboard_snapshot() -> dict:
    if not trading_engine:
        return {"error": "Engine not running"}

    positions = {}
    for position_manager in trading_engine.position_managers.values():
        positions.update(position_manager.positions)
    nav = sum(rm.current_capital for rm in trading_engine.risk_managers.values())
    daily_pnl = sum(rm.daily_pnl for rm in trading_engine.risk_managers.values())
    open_risk = sum(rm.open_risk for rm in trading_engine.risk_managers.values())
    latest_prices = {}
    for broker_prices in trading_engine.broker_latest_prices.values():
        latest_prices.update(broker_prices)

    unrealized_pnl = 0.0
    position_summary = []
    for position in positions.values():
        current_price = latest_prices.get(position.symbol, position.entry_price)
        pnl = (
            (current_price - position.entry_price) * position.quantity
            if position.side == "BUY"
            else (position.entry_price - current_price) * position.quantity
        )
        unrealized_pnl += pnl
        position_summary.append(
            {
                "symbol": position.symbol,
                "side": position.side,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "current_price": current_price,
                "unrealized_pnl": pnl,
            }
        )

    return {
        "status": "running" if trading_engine.is_running else "stopped",
        "nav": nav,
        "daily_pnl": daily_pnl,
        "unrealized_pnl": unrealized_pnl,
        "open_risk": open_risk,
        "open_positions": len(positions),
        "recent_trades": trading_engine.trade_results[-10:],
        "positions": position_summary,
    }


def _is_configured() -> bool:
    """Check if the bot has been configured (has broker credentials)."""
    env_path = Path(".env")
    if not env_path.exists():
        return False
    with open(env_path) as f:
        content = f.read()
    markers = [
        "IB_ACCOUNT_ID=",
        "BINANCE_API_KEY=",
        "OKX_API_KEY=",
        "COINBASE_API_KEY=",
        "KRAKEN_API_KEY=",
        "KUCOIN_API_KEY=",
    ]
    return any(marker in content for marker in markers)


def set_trading_engine(engine):
    global trading_engine
    trading_engine = engine
    log.info("Trading engine registered with API")


@app.get("/")
async def root_redirect():
    if not _is_configured():
        return FileResponse("config/setup.html")
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health_check():
    return {"status": "ok", "bot_name": CONFIG["general"]["bot_name"]}


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
    except Exception:  # noqa: BLE001
        return {"status": "error", "message": "Internal error"}


@app.get("/api/signals")
async def api_signals(symbol: str = Query(..., description="Ticker symbol")):
    if not trading_engine:
        return {"error": "Engine not running"}
    now_utc = datetime.now(timezone.utc)

    # Route crypto symbols to ccxt, stocks to Yahoo
    if trading_engine._is_crypto(symbol):
        df = pd.DataFrame()
        for broker_name, broker in trading_engine.broker_manager.iterate_all():
            if broker_name not in trading_engine.risk_managers:
                continue
            if symbol in trading_engine.symbols_by_broker.get(broker_name, []):
                df = trading_engine._get_crypto_data(broker, symbol, limit=200)
                break
    else:
        df = trading_engine.data_manager.get_data(
            symbol,
            start_date=(now_utc - timedelta(days=7)).strftime("%Y-%m-%d"),
            end_date=now_utc.strftime("%Y-%m-%d"),
            interval="15m",
            force_refresh=True,
        )

    if df.empty:
        return {"error": "No data"}
    signals = [
        str(strat.generate_signals(df).iloc[-1]) for strat in trading_engine.strategies
    ]
    return {
        "symbol": symbol,
        "signals": signals,
        "last_price": float(df["close"].iloc[-1]),
    }


@app.get("/api/first-run")
async def api_first_run():
    if trading_engine:
        return {"first_run": trading_engine.first_run}
    return {"first_run": False}


@app.post("/api/assistant")
async def api_assistant(request: Request):
    data = await request.json()
    question = str(data.get("question", "")).strip()
    if not question:
        return {"error": "Enter a question first."}

    api_key = os.getenv("AI_API_KEY")
    if not api_key:
        return {
            "error": "AI assistant is not configured. Set AI_API_KEY, AI_API_URL, and AI_MODEL in .env."
        }

    snapshot = _dashboard_snapshot()
    api_url = os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions")
    model = os.getenv("AI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the IrieTrade dashboard assistant. Explain the current "
                    "portfolio snapshot clearly and conservatively. Do not invent data, "
                    "recommend trades, or claim to have placed orders. Mention when data "
                    "is missing or stale. Keep answers under 180 words."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {question}\nDashboard snapshot:\n{json.dumps(snapshot, default=str)}",
            },
        ],
    }
    try:
        result = await asyncio.to_thread(_post_ai_request, api_url, api_key, payload)
        answer = result["choices"][0]["message"]["content"].strip()
        return {"answer": answer, "model": model}
    except (HTTPError, URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        log.warning(f"Dashboard assistant request failed: {exc}")
        return {"error": "The AI assistant could not respond right now."}


@app.get("/api/positions")
async def api_positions():
    if not trading_engine:
        return {"error": "Engine not running"}
    positions = []
    for pm in trading_engine.position_managers.values():
        for pos in pm.positions.values():
            positions.append(
                {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "stop_loss": pos.stop_loss,
                }
            )
    return {"positions": positions}


@app.get("/api/performance")
async def api_performance():
    if not trading_engine:
        return {"error": "Engine not running"}
    nav = sum(rm.current_capital for rm in trading_engine.risk_managers.values())
    daily_pnl = sum(rm.daily_pnl for rm in trading_engine.risk_managers.values())
    open_risk_val = sum(rm.open_risk for rm in trading_engine.risk_managers.values())
    open_positions = sum(
        len(pm.positions) for pm in trading_engine.position_managers.values()
    )
    return {
        "nav": nav,
        "daily_pnl": daily_pnl,
        "open_risk": open_risk_val,
        "open_positions": open_positions,
    }


@app.post("/api/webhook/tradingview")
async def tradingview_webhook(request: Request):
    """Receive a TradingView alert and pass it to the engine."""
    if not trading_engine:
        return {"error": "Engine not running"}

    try:
        data = await request.json()
        symbol = data.get("symbol", "").upper()
        action = data.get("action", "").upper()
        quantity = int(data.get("quantity", 0))
        price = float(data.get("price", 0))

        if not symbol or action not in ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"):
            return {"error": "Invalid payload"}

        broker_name = "ib"
        if broker_name not in trading_engine.risk_managers:
            return {"error": "IBKR broker not available"}

        broker = trading_engine.broker_manager.brokers[broker_name]
        pm = trading_engine.position_managers[broker_name]

        success = trading_engine._place_trade(
            broker,
            pm,
            symbol,
            action,
            quantity,
            price,
            stop_loss=0.0,
            atr=0.0,
            vol_stop_mult=0.0,
        )
        if success:
            log.success(f"Webhook trade executed: {action} {quantity} {symbol}")
            return {"status": "ok", "message": f"Trade placed: {action} {quantity} {symbol}"}
        return {"error": "Trade rejected by risk manager or broker"}

    except Exception as e:  # noqa: BLE001
        log.exception(f"Webhook error while processing TradingView alert: {e}")
        return {"error": "Internal webhook processing error"}


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

    df = pd.DataFrame(history, columns=["time", "nav"])
    df.set_index("time", inplace=True)
    df = df.sort_index()

    last_nav = df["nav"].iloc[-1] if len(df) else nav
    first_nav = df["nav"].iloc[0] if len(df) else nav
    total_return = (last_nav - first_nav) / first_nav * 100 if first_nav else 0
    daily_change = df["nav"].iloc[-1] - df["nav"].iloc[-2] if len(df) > 1 else 0
    denom = df["nav"].iloc[-2]
    daily_pct = (daily_change / denom) * 100 if len(df) > 1 and denom != 0 else 0.0
    current_capital = nav

    latest_prices = {}
    for bp in trading_engine.broker_latest_prices.values():
        latest_prices.update(bp)
    computed_unrealized = 0.0
    for pos in positions.values():
        price = latest_prices.get(pos.symbol, pos.entry_price)
        if pos.side == "BUY":
            computed_unrealized += (price - pos.entry_price) * pos.quantity
        else:
            computed_unrealized += (pos.entry_price - price) * pos.quantity
    unrealized_pnl = computed_unrealized
    realized_pnl = getattr(trading_engine, "realized_pnl", 0.0)
    trading_total = unrealized_pnl + realized_pnl
    interest_effect = (last_nav - first_nav) - trading_total

    rolling_sharpe = None
    if len(df) >= 30:
        daily_returns = df["nav"].pct_change().dropna()
        if len(daily_returns) >= 30:
            sharpe_series = (
                daily_returns.rolling(30).mean() / daily_returns.rolling(30).std()
            ) * (252**0.5)
            rolling_sharpe = sharpe_series.iloc[-1] if not sharpe_series.empty else None
    sharpe_display = f"{rolling_sharpe:.2f}" if rolling_sharpe is not None else "—"

    bot_status = "Running" if trading_engine.is_running else "Stopped"
    portfolio_heat = open_risk_val / current_capital * 100 if current_capital else 0.0

    # Plotly chart
    fig = make_subplots(rows=1, cols=1)
    color_line = "#00b894" if total_return >= 0 else "#e17055"
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["nav"],
            mode="lines",
            line={"color": color_line, "width": 2},
            fill="tozeroy",
            fillcolor="rgba(0, 184, 148, 0.1)"
            if total_return >= 0
            else "rgba(225, 112, 85, 0.1)",
            name="NAV",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title={
            "text": "IrieTrade Equity Curve",
            "x": 0.05,
            "font": {"size": 24, "family": "Arial Black"},
        },
        xaxis={"showgrid": False, "zeroline": False},
        yaxis={
            "title": "Net Asset Value (USD)",
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.05)",
            "zeroline": False,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#dfe6e9"},
        margin={"l": 20, "r": 20, "t": 60, "b": 20},
        hovermode="x unified",
    )
    plot_html = fig.to_html(full_html=False, config={"responsive": True})

    # Positions table
    position_rows = ""
    for pos in positions.values():
        current_price = latest_prices.get(pos.symbol, pos.entry_price)
        if pos.side == "BUY":
            pnl = (current_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - current_price) * pos.quantity
        pnl_class = "metric-positive" if pnl >= 0 else "metric-negative"
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
        recent_trades_html = (
            "<ul style='list-style:none; padding:0; color:#b0becd; font-size:14px;'>"
        )
        for result_type, pnl_frac in trading_engine.trade_results[-10:]:
            color = "#00d084" if result_type == "win" else "#ff7c7c"
            recent_trades_html += f"<li style='margin:4px 0;'><span style='color:{color};'>{result_type.upper()}</span> - {pnl_frac:+.2%}</li>"
        recent_trades_html += "</ul>"
    else:
        recent_trades_html = "<p>No closed trades yet.</p>"

    now_utc = datetime.now(timezone.utc)

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="60">
        <meta name="theme-color" content="#121416">
        <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%23121416'/%3E%3Cpath d='M16 44 27 20l8 14 6-10 7 20' fill='none' stroke='%2343d18b' stroke-linecap='round' stroke-linejoin='round' stroke-width='6'/%3E%3C/svg%3E">
        <title>Irie Trade – Live Dashboard</title>
        <style>
            :root {{
                color-scheme: light;
                --bg: #f4f5f7;
                --surface: #ffffff;
                --surface-muted: #f8f9fb;
                --border: #e7e9ee;
                --text: #15171a;
                --muted: #7d8490;
                --accent: #121416;
                --positive: #159a62;
                --negative: #d94f5c;
                --shadow: 0 14px 40px rgba(28, 35, 45, 0.07);
                font-family: 'Archivo', 'Segoe UI', sans-serif;
                background: var(--bg);
                color: var(--text);
            }}
            :root[data-theme="dark"] {{
                color-scheme: dark;
                --bg: #0d1015;
                --surface: #151922;
                --surface-muted: #1b202a;
                --border: #292f3a;
                --text: #f5f7fa;
                --muted: #9ca5b3;
                --accent: #f5f7fa;
                --positive: #43d18b;
                --negative: #ff7a86;
                --shadow: 0 16px 44px rgba(0, 0, 0, 0.24);
            }}
            * {{ box-sizing: border-box; }}
            html {{ scroll-behavior: smooth; }}
            body {{
                margin: 0;
                min-height: 100vh;
                background: var(--bg);
                color: var(--text);
                transition: background 180ms ease, color 180ms ease;
            }}
            button, a {{ -webkit-tap-highlight-color: transparent; }}
            button:focus-visible, a:focus-visible {{ outline: 3px solid rgba(21, 154, 98, 0.3); outline-offset: 2px; }}
            .layout {{ display: grid; grid-template-columns: 224px minmax(0, 1fr); gap: 30px; max-width: 1560px; margin: 0 auto; padding: 28px 32px 40px; transition: grid-template-columns 220ms ease; }}
            .layout.nav-collapsed {{ grid-template-columns: 72px minmax(0, 1fr); }}
            .sidebar {{ position: sticky; top: 28px; height: calc(100vh - 56px); background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 24px 16px; display: flex; flex-direction: column; box-shadow: var(--shadow); overflow: hidden; }}
            .brand {{ display: flex; align-items: center; gap: 11px; padding: 0 10px 34px; }}
            .brand-mark {{ display: grid; place-items: center; width: 30px; height: 30px; flex: 0 0 30px; border-radius: 9px; background: var(--accent); color: var(--surface); }}
            .brand-mark svg {{ width: 22px; height: 22px; }}
            .brand-title {{ font-size: 21px; font-weight: 700; letter-spacing: -0.04em; }}
            .brand-toggle {{ display: grid; place-items: center; width: 32px; height: 32px; margin-left: auto; border: 0; border-radius: 8px; background: transparent; color: var(--muted); cursor: pointer; }}
            .brand-toggle:hover {{ background: var(--surface-muted); color: var(--text); }}
            .brand-toggle svg {{ width: 17px; height: 17px; }}
            .nav-label {{ padding: 0 12px 10px; color: var(--muted); font-size: 10px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; }}
            .nav-item {{ display: flex; align-items: center; gap: 11px; min-height: 42px; padding: 0 12px; margin: 2px 0; border-radius: 9px; color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 600; transition: background 180ms ease, color 180ms ease; }}
            .nav-item svg {{ width: 16px; height: 16px; flex: 0 0 16px; }}
            .nav-item.active, .nav-item:hover {{ background: var(--surface-muted); color: var(--text); }}
            .sidebar-footer {{ margin-top: auto; padding: 16px 12px 0; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; line-height: 1.5; }}
            .nav-collapsed .brand {{ padding-left: 4px; padding-right: 4px; }}
            .nav-collapsed .brand-title, .nav-collapsed .nav-label, .nav-collapsed .nav-item span, .nav-collapsed .sidebar-footer {{ display: none; }}
            .nav-collapsed .brand {{ justify-content: center; }}
            .nav-collapsed .nav-item {{ justify-content: center; padding: 0; }}
            main {{ min-width: 0; }}
            .topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 28px; }}
            .eyebrow {{ color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }}
            .topbar h2 {{ margin: 5px 0 0; font-size: 24px; letter-spacing: -0.04em; }}
            .topbar-actions {{ display: flex; align-items: center; gap: 10px; }}
            .theme-toggle, .icon-button {{ min-height: 40px; padding: 0 14px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface); color: var(--text); cursor: pointer; font: inherit; font-size: 12px; font-weight: 700; transition: background 180ms ease, border-color 180ms ease, transform 180ms ease; }}
            .icon-button {{ display: grid; place-items: center; width: 40px; padding: 0; }}
            .icon-button svg {{ width: 17px; height: 17px; }}
            .theme-toggle:hover {{ background: var(--surface-muted); }}
            .theme-toggle:hover, .icon-button:hover {{ transform: translateY(-1px); }}
            .theme-toggle:focus-visible {{ outline: 3px solid rgba(21, 154, 98, 0.25); outline-offset: 2px; }}
            .status-pill {{ display: inline-flex; align-items: center; gap: 7px; min-height: 34px; padding: 0 11px; border-radius: 999px; background: var(--surface-muted); color: var(--text); font-size: 12px; font-weight: 700; }}
            .status-pill::before {{ content: ''; width: 7px; height: 7px; border-radius: 50%; background: var(--positive); }}
            .panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 26px; margin-bottom: 20px; box-shadow: var(--shadow); }}
            .panel-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 22px; }}
            .panel-header h1 {{ margin: 0; font-size: 22px; letter-spacing: -0.04em; }}
            .panel-header p {{ margin: 7px 0 0; color: var(--muted); font-size: 13px; }}
            .panel-tools {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
            .range-control {{ display: inline-flex; align-items: center; gap: 2px; padding: 3px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-muted); }}
            .range-button {{ min-height: 30px; padding: 0 9px; border: 0; border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: 11px; font-weight: 700; }}
            .range-button:hover, .range-button.active {{ background: var(--surface); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
            .stat-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--border); }}
            .stat-card {{ min-width: 0; background: var(--surface); padding: 18px; cursor: pointer; transition: background 180ms ease, transform 180ms ease; }}
            .stat-card:hover {{ background: var(--surface-muted); transform: translateY(-2px); }}
            .stat-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 11px; }}
            .stat-value {{ font-size: 24px; font-weight: 700; line-height: 1.1; letter-spacing: -0.04em; overflow-wrap: anywhere; }}
            .stat-subtext {{ margin-top: 8px; color: var(--muted); font-size: 11px; }}
            .metric-positive {{ color: var(--positive); }}
            .metric-negative {{ color: var(--negative); }}
            .asset-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
            .asset-card {{ background: var(--surface-muted); border: 1px solid var(--border); border-radius: 12px; padding: 17px; }}
            .asset-card h3 {{ margin: 0; font-size: 13px; font-weight: 700; }}
            .asset-value {{ font-size: 20px; font-weight: 700; margin-top: 14px; letter-spacing: -0.03em; }}
            .asset-change {{ margin-top: 7px; color: var(--muted); font-size: 11px; line-height: 1.4; }}
            .positions-table {{ width: 100%; border-collapse: collapse; margin-top: 4px; }}
            .positions-table th, .positions-table td {{ padding: 14px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; white-space: nowrap; }}
            .positions-table th {{ color: var(--muted); font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }}
            .positions-table tr:last-child td {{ border-bottom: 0; }}
            .positions-empty td {{ color: var(--muted); text-align: center; }}
            .chart-card {{ min-height: 420px; padding-top: 18px; }}
            .chart-card .js-plotly-plot {{ min-height: 380px; }}
            .insight-dialog {{ width: min(440px, calc(100vw - 32px)); border: 1px solid var(--border); border-radius: 16px; padding: 0; background: var(--surface); color: var(--text); box-shadow: 0 24px 80px rgba(0,0,0,0.22); }}
            .insight-dialog::backdrop {{ background: rgba(9, 12, 18, 0.46); backdrop-filter: blur(4px); }}
            .dialog-inner {{ padding: 24px; }}
            .dialog-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
            .dialog-header h2 {{ margin: 0; font-size: 18px; }}
            .dialog-close {{ border: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 20px; line-height: 1; }}
            .dialog-copy {{ margin: 18px 0 0; color: var(--muted); font-size: 13px; line-height: 1.6; }}
            .assistant-bar {{ display: flex; gap: 8px; align-items: center; margin: 0 0 20px; padding: 8px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); box-shadow: var(--shadow); }}
            .assistant-bar input {{ flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; font-size: 13px; padding: 8px 10px; }}
            .assistant-bar button {{ min-height: 34px; padding: 0 14px; border: 0; border-radius: 8px; background: var(--accent); color: var(--surface); cursor: pointer; font: inherit; font-size: 12px; font-weight: 700; }}
            .assistant-bar button:disabled {{ opacity: 0.55; cursor: wait; }}
            .assistant-answer {{ margin: -8px 0 20px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; color: var(--muted); background: var(--surface-muted); font-size: 13px; line-height: 1.5; white-space: pre-wrap; }}
            .footer {{ margin-top: 26px; text-align: center; color: var(--muted); font-size: 11px; }}
            @media (max-width: 1180px) {{ .layout {{ grid-template-columns: 190px minmax(0, 1fr); padding: 22px; gap: 20px; }} .stat-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .asset-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
            @media (max-width: 760px) {{ .layout, .layout.nav-collapsed {{ display: block; padding: 14px; }} .sidebar {{ position: static; height: auto; margin-bottom: 18px; padding: 14px; }} .brand, .nav-collapsed .brand {{ padding: 4px 8px 16px; justify-content: flex-start; }} .brand-title, .nav-collapsed .brand-title, .brand-toggle, .nav-collapsed .brand-toggle, .nav-label, .sidebar-footer {{ display: none; }} .sidebar nav, .nav-collapsed .sidebar nav {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; }} .nav-item, .nav-collapsed .nav-item {{ justify-content: center; padding: 0 6px; text-align: center; font-size: 11px; }} .nav-item span, .nav-collapsed .nav-item span {{ display: inline; }} .topbar {{ align-items: flex-start; margin-bottom: 18px; }} .topbar h2 {{ font-size: 20px; }} .panel {{ padding: 18px; border-radius: 13px; }} .panel-header {{ flex-direction: column; }} .stat-grid, .asset-grid {{ grid-template-columns: 1fr 1fr; }} .stat-card {{ padding: 14px; }} .stat-value {{ font-size: 20px; }} .chart-card {{ min-height: 320px; overflow: hidden; }} .assistant-bar {{ margin-bottom: 16px; }} }}
            @media (max-width: 420px) {{ .stat-grid, .asset-grid {{ grid-template-columns: 1fr; }} .topbar-actions {{ flex-direction: column; align-items: flex-end; }} .theme-toggle {{ min-height: 36px; }} }}
            @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: 0.01ms !important; }} }}
        </style>
    </head>
    <body>
        <div class="layout">
            <aside class="sidebar">
                <div class="brand">
                    <div class="brand-mark" aria-label="IrieTrade logo">
                        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M4 17 8.5 7l3.3 6 2.6-4 2.9 8" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2"/>
                        </svg>
                    </div>
                    <div class="brand-title">IrieTrade</div>
                    <button class="brand-toggle" id="sidebar-toggle" type="button" aria-label="Collapse navigation" aria-expanded="true">
                        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 6 15 12 9 18" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/></svg>
                    </button>
                </div>
                <div class="nav-label">Workspace</div>
                <nav aria-label="Dashboard navigation">
                    <a class="nav-item active" href="#overview"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.8"/><rect x="14" y="4" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.8"/><rect x="4" y="14" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.8"/><rect x="14" y="14" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.8"/></svg><span>Overview</span></a>
                    <a class="nav-item" href="#positions"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 19V9m7 10V5m7 14v-7" stroke="currentColor" stroke-linecap="round" stroke-width="1.8"/></svg><span>Portfolio</span></a>
                    <a class="nav-item" href="#alerts"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 4h9l3 3v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-linejoin="round" stroke-width="1.8"/><path d="M8 10h8M8 14h8M8 18h5" stroke="currentColor" stroke-linecap="round" stroke-width="1.6"/></svg><span>Ledger</span></a>
                    <a class="nav-item" href="/setup"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" stroke="currentColor" stroke-width="1.8"/><path d="m19.4 15 .1.1a2 2 0 0 1-2.8 2.8l-.1-.1a2 2 0 0 0-3.4 1.4v.2a2 2 0 0 1-4 0v-.2A2 2 0 0 0 5.8 17.8l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A2 2 0 0 0 1.6 11.6h-.2a2 2 0 0 1 0-4h.2A2 2 0 0 0 3 4.2l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A2 2 0 0 0 9.2 0h.2a2 2 0 0 1 4 0v.2A2 2 0 0 0 16.8 1.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a2 2 0 0 0 1.4 3.4h.2a2 2 0 0 1 0 4H21a2 2 0 0 0-1.6 3.4Z" transform="scale(.72) translate(4.5 4.5)" stroke="currentColor" stroke-width="1.8"/></svg><span>Settings</span></a>
                </nav>
                <div class="sidebar-footer">Read-only dashboard<br>Updates every hour</div>
            </aside>
            <main>
                <header class="topbar">
                    <div>
                        <div class="eyebrow">Trading workspace</div>
                        <h2>Portfolio overview</h2>
                    </div>
                    <div class="topbar-actions">
                        <div class="status-pill">{bot_status}</div>
                        <button class="icon-button" id="refresh-button" type="button" aria-label="Refresh dashboard now" title="Refresh dashboard now">
                            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.7-4L4 9m0 0V4m0 5h5M4 13a8 8 0 0 0 14.7 4L20 15m0 0v5m0-5h-5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8"/></svg>
                        </button>
                        <button class="theme-toggle" id="theme-toggle" type="button" aria-label="Switch color theme">Dark mode</button>
                    </div>
                </header>
                <form class="assistant-bar" id="assistant-form">
                    <input id="assistant-question" type="text" placeholder="Ask about the current portfolio" aria-label="Ask the dashboard assistant">
                    <button type="submit" id="assistant-submit">Ask assistant</button>
                </form>
                <div class="assistant-answer" id="assistant-answer" hidden></div>
                <section id="overview" class="panel">
                    <div class="panel-header">
                        <div>
                            <h1>Live portfolio overview</h1>
                            <p>Monitor NAV, risk, positions, and bot health without interrupting execution.</p>
                        </div>
                    </div>
                    <div class="stat-grid">
                        <div class="stat-card" tabindex="0" data-metric="Net Asset Value" data-detail="Current portfolio value across all available broker accounts.">
                            <div class="stat-label">Net Asset Value</div>
                            <div class="stat-value">${last_nav:,.2f}</div>
                            <div class="stat-subtext">Current portfolio value</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Daily P&L" data-detail="The combined profit or loss recorded by all broker risk managers for the current trading day.">
                            <div class="stat-label">Daily P&L</div>
                            <div class="stat-value {("metric-positive" if daily_pnl >= 0 else "metric-negative")}">{daily_pnl:+,.2f}</div>
                            <div class="stat-subtext">{daily_pct:+.2f}% change</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Unrealised P&L" data-detail="The mark-to-market profit or loss calculated from the latest prices and open positions.">
                            <div class="stat-label">Unrealised P&L</div>
                            <div class="stat-value {("metric-positive" if unrealized_pnl >= 0 else "metric-negative")}">{unrealized_pnl:+,.2f}</div>
                            <div class="stat-subtext">Mark‑to‑market</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Trading P&L" data-detail="Unrealised plus realised trading performance, excluding interest and dividend effects.">
                            <div class="stat-label">Trading P&L (U+R)</div>
                            <div class="stat-value {("metric-positive" if trading_total >= 0 else "metric-negative")}">{trading_total:+,.2f}</div>
                            <div class="stat-subtext">Excludes interest & dividends</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Interest & Div Effect" data-detail="The portion of NAV movement not explained by trading performance.">
                            <div class="stat-label">Interest & Div Effect</div>
                            <div class="stat-value {("metric-positive" if interest_effect >= 0 else "metric-negative")}">{interest_effect:+,.2f}</div>
                            <div class="stat-subtext">NAV change driven by cash</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Rolling Sharpe" data-detail="A 30-day annualised risk-adjusted return estimate when enough NAV history is available.">
                            <div class="stat-label">30-Day Rolling Sharpe</div>
                            <div class="stat-value {("metric-positive" if rolling_sharpe is not None and rolling_sharpe >= 0 else "metric-negative")}">{sharpe_display}</div>
                            <div class="stat-subtext">Risk-adjusted return (annualised)</div>
                        </div>
                        <div class="stat-card" tabindex="0" data-metric="Portfolio Heat" data-detail="Open risk as a percentage of current capital. Lower values leave more room for new trades.">
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
                            <div class="asset-value {("metric-positive" if unrealized_pnl >= 0 else "metric-negative")}">{unrealized_pnl:+,.2f}</div>
                            <div class="asset-change">Broker unrealized P&L</div>
                        </div>
                        <div class="asset-card">
                            <h3>Positions</h3>
                            <div class="asset-value">{open_pos}</div>
                            <div class="asset-change">Active position count</div>
                        </div>
                        <div class="asset-card">
                            <h3>Latest refresh</h3>
                            <div class="asset-value">{now_utc.strftime("%H:%M:%S")}</div>
                            <div class="asset-change">Real-time dashboard snapshot</div>
                        </div>
                    </div>
                </section>

                <section class="panel chart-card">
                    <div class="panel-header">
                        <div>
                            <h1>Equity curve</h1>
                            <p>Portfolio NAV over the recorded trading history.</p>
                        </div>
                        <div class="panel-tools">
                            <div class="range-control" role="group" aria-label="Chart time range">
                                <button class="range-button" data-range="7" type="button">1W</button>
                                <button class="range-button active" data-range="30" type="button">1M</button>
                                <button class="range-button" data-range="90" type="button">3M</button>
                                <button class="range-button" data-range="365" type="button">1Y</button>
                                <button class="range-button" data-range="all" type="button">All</button>
                            </div>
                        </div>
                    </div>
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

                <section id="alerts" class="panel">
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
    <dialog class="insight-dialog" id="insight-dialog">
        <div class="dialog-inner">
            <div class="dialog-header">
                <h2 id="dialog-title">Metric detail</h2>
                <button class="dialog-close" type="button" aria-label="Close metric detail">&times;</button>
            </div>
            <p class="dialog-copy" id="dialog-copy"></p>
        </div>
    </dialog>
    <script>
        const root = document.documentElement;
        const themeToggle = document.getElementById('theme-toggle');
        const savedTheme = localStorage.getItem('irietrade-theme');
        const preferredTheme = savedTheme || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

        function applyTheme(theme) {{
            root.dataset.theme = theme;
            themeToggle.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
            themeToggle.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
            localStorage.setItem('irietrade-theme', theme);
        }}

        applyTheme(preferredTheme);
        themeToggle.addEventListener('click', () => applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'));

        const layout = document.querySelector('.layout');
        const sidebarToggle = document.getElementById('sidebar-toggle');
        const savedNavState = localStorage.getItem('irietrade-nav-collapsed') === 'true';
        if (savedNavState) {{
            layout.classList.add('nav-collapsed');
            sidebarToggle.setAttribute('aria-expanded', 'false');
            sidebarToggle.setAttribute('aria-label', 'Expand navigation');
        }}
        sidebarToggle.addEventListener('click', () => {{
            const collapsed = layout.classList.toggle('nav-collapsed');
            sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
            sidebarToggle.setAttribute('aria-label', collapsed ? 'Expand navigation' : 'Collapse navigation');
            localStorage.setItem('irietrade-nav-collapsed', String(collapsed));
        }});

        const insightDialog = document.getElementById('insight-dialog');
        const dialogTitle = document.getElementById('dialog-title');
        const dialogCopy = document.getElementById('dialog-copy');
        const closeDialog = () => insightDialog.close();
        document.querySelectorAll('.stat-card[data-metric]').forEach((card) => {{
            const openDialog = () => {{
                dialogTitle.textContent = card.dataset.metric;
                dialogCopy.textContent = card.dataset.detail;
                insightDialog.showModal();
            }};
            card.addEventListener('click', openDialog);
            card.addEventListener('keydown', (event) => {{
                if (event.key === 'Enter' || event.key === ' ') {{
                    event.preventDefault();
                    openDialog();
                }}
            }});
        }});
        insightDialog.querySelector('.dialog-close').addEventListener('click', closeDialog);
        insightDialog.addEventListener('click', (event) => {{
            if (event.target === insightDialog) closeDialog();
        }});

        document.getElementById('refresh-button').addEventListener('click', (event) => {{
            const button = event.currentTarget;
            button.disabled = true;
            button.style.opacity = '0.55';
            window.location.reload();
        }});

        const assistantForm = document.getElementById('assistant-form');
        const assistantQuestion = document.getElementById('assistant-question');
        const assistantSubmit = document.getElementById('assistant-submit');
        const assistantAnswer = document.getElementById('assistant-answer');
        assistantForm.addEventListener('submit', async (event) => {{
            event.preventDefault();
            const question = assistantQuestion.value.trim();
            if (!question) return;
            assistantSubmit.disabled = true;
            assistantAnswer.hidden = false;
            assistantAnswer.textContent = 'Reviewing the current dashboard snapshot...';
            try {{
                const response = await fetch('/api/assistant', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ question }})
                }});
                const result = await response.json();
                assistantAnswer.textContent = result.answer || result.error || 'No assistant response.';
            }} catch (error) {{
                assistantAnswer.textContent = 'The assistant request failed. Check the API configuration and try again.';
            }} finally {{
                assistantSubmit.disabled = false;
            }}
        }});

        const chart = document.querySelector('.js-plotly-plot');
        document.querySelectorAll('.range-button').forEach((button) => {{
            button.addEventListener('click', () => {{
                document.querySelectorAll('.range-button').forEach((item) => item.classList.remove('active'));
                button.classList.add('active');
                if (!chart || !window.Plotly) return;
                if (button.dataset.range === 'all') {{
                    Plotly.relayout(chart, {{'xaxis.autorange': true}});
                    return;
                }}
                const chartEnd = chart.data?.[0]?.x?.at(-1);
                const end = chartEnd ? new Date(chartEnd) : new Date();
                const start = new Date(end);
                start.setDate(end.getDate() - Number(button.dataset.range));
                Plotly.relayout(chart, {{'xaxis.autorange': false, 'xaxis.range': [start, end]}});
            }});
        }});

        const sections = [...document.querySelectorAll('main section[id]')];
        const navLinks = [...document.querySelectorAll('.nav-item[href^="#"]')];
        const sectionObserver = new IntersectionObserver((entries) => {{
            entries.forEach((entry) => {{
                if (!entry.isIntersecting) return;
                navLinks.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${{entry.target.id}}`));
            }});
        }}, {{ rootMargin: '-20% 0px -65% 0px', threshold: 0 }});
        sections.forEach((section) => sectionObserver.observe(section));

        fetch('/api/first-run')
            .then(r => r.json())
            .then(data => {{
                if (data.first_run) {{
                    const toast = document.createElement('div');
                    toast.style.cssText = 'position:fixed;bottom:20px;right:20px;background:linear-gradient(135deg,#2d3436,#636e72);color:#fff;padding:20px 28px;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.3);z-index:9999;font-size:14px;max-width:340px;';
                    toast.innerHTML = `
                        <strong>👋 Welcome to IrieTrade!</strong><br><br>
                        ⭐ <a href="https://github.com/Native-254/IrieTrade" target="_blank" style="color:#fdcb6e;">Star the repo</a><br>
                        💬 <a href="https://github.com/Native-254/IrieTrade/discussions" target="_blank" style="color:#74b9ff;">Join Discussions</a><br>
                        ✉️ <a href="mailto:info.native@gmail.com" style="color:#ff7675;">Send a suggestion</a><br><br>
                        <button onclick="this.parentElement.remove()" style="background:#dfe6e9;color:#2d3436;border:none;padding:6px 14px;border-radius:8px;cursor:pointer;">Got it ✌️</button>
                    `;
                    document.body.appendChild(toast);
                }}
            }});
    </script>
    </body>
    </html>
    """)


# ---------- Onboarding / Setup ----------
def test_broker_connection(broker_name: str, credentials: dict) -> bool:
    """Quickly test if a broker can connect with the given credentials."""
    try:
        if broker_name == "ib":
            broker = IBBroker()
            broker.config["account_id"] = credentials["account_id"]
            broker.connect()
        elif broker_name == "binance":
            broker = BinanceBroker({"testnet": False})
            broker.api_key = credentials["api_key"]
            broker.secret = credentials["secret"]
            broker.connect()
        elif broker_name == "okx":
            broker = OKXBroker({"testnet": False})
            broker.api_key = credentials["api_key"]
            broker.secret = credentials["secret"]
            broker.password = credentials.get("passphrase", "")
            broker.connect()
        elif broker_name == "coinbase":
            broker = CoinbaseBroker({"testnet": False})
            broker.api_key = credentials["api_key"]
            broker.secret = credentials["secret"]
            broker.password = credentials.get("passphrase", "")
            broker.connect()
        elif broker_name == "kraken":
            broker = KrakenBroker({"testnet": False})
            broker.api_key = credentials["api_key"]
            broker.secret = credentials["secret"]
            broker.connect()
        elif broker_name == "kucoin":
            broker = KucoinBroker({"testnet": False})
            broker.api_key = credentials["api_key"]
            broker.secret = credentials["secret"]
            broker.password = credentials.get("passphrase", "")
            broker.connect()
        else:
            return False
        broker.get_account_info()
        broker.disconnect()
        return True
    except Exception:  # noqa: BLE001
        return False


@app.post("/api/setup/validate")
async def setup_validate(request: Request):
    data = await request.json()
    brokers = data.get("brokers", [])
    credentials = data.get("credentials", {})

    for broker in brokers:
        if not test_broker_connection(broker, credentials.get(broker, {})):
            return {"success": False, "message": f"Connection failed for {broker}"}
    return {"success": True, "message": "All connections successful"}


@app.post("/api/setup/save")
async def setup_save(request: Request):
    data = await request.json()
    brokers = data.get("brokers", [])
    credentials = data.get("credentials", {})
    symbols = data.get("symbols", [])

    # 1. Write .env file
    env_path = Path(".env")
    with open(env_path, "a") as f:  # noqa: ASYNC230
        for broker in brokers:
            if broker == "ib":
                val = credentials[broker]["account_id"]
                f.write(f"IB_ACCOUNT_ID={encrypt(val)}\n")
            elif broker == "binance":
                f.write(f"BINANCE_API_KEY={encrypt(credentials[broker]['api_key'])}\n")
                f.write(f"BINANCE_SECRET={encrypt(credentials[broker]['secret'])}\n")
            elif broker == "okx":
                f.write(f"OKX_API_KEY={encrypt(credentials[broker]['api_key'])}\n")
                f.write(f"OKX_SECRET={encrypt(credentials[broker]['secret'])}\n")
                f.write(f"OKX_PASSPHRASE={encrypt(credentials[broker].get('passphrase', ''))}\n")
            elif broker == "coinbase":
                f.write(f"COINBASE_API_KEY={encrypt(credentials[broker]['api_key'])}\n")
                f.write(f"COINBASE_SECRET={encrypt(credentials[broker]['secret'])}\n")
                f.write(f"COINBASE_PASSPHRASE={encrypt(credentials[broker].get('passphrase', ''))}\n")
            elif broker == "kraken":
                f.write(f"KRAKEN_API_KEY={encrypt(credentials[broker]['api_key'])}\n")
                f.write(f"KRAKEN_SECRET={encrypt(credentials[broker]['secret'])}\n")
            elif broker == "kucoin":
                f.write(f"KUCOIN_API_KEY={encrypt(credentials[broker]['api_key'])}\n")
                f.write(f"KUCOIN_SECRET={encrypt(credentials[broker]['secret'])}\n")
                f.write(f"KUCOIN_PASSPHRASE={encrypt(credentials[broker].get('passphrase', ''))}\n")

    # 2. Update settings.yaml with brokers and symbols
    config_path = Path("config/settings.yaml")
    with open(config_path) as f:  # noqa: ASYNC230
        config = yaml.safe_load(f)
    config["trading"]["platforms"] = brokers
    config["trading"]["platform"] = brokers[0]
    config["trading"]["symbols"] = symbols
    with open(config_path, "w") as f:  # noqa: ASYNC230
        yaml.dump(config, f)

    # 3. Signal the engine to restart with new config
    if trading_engine:
        trading_engine.restart_with_new_config(config)

    return {"success": True, "message": "Configuration saved and bot restarted"}


@app.get("/setup")
async def setup_page():
    return FileResponse("config/setup.html")


def run_api():
    port = CONFIG["monitoring"]["health_check_port"]
    log.info(f"Starting health check API on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
