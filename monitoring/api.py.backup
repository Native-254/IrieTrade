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
        pnl_class = "up" if pnl >= 0 else "down"
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

    template_path = Path(__file__).with_name("dashboard.html")
    dashboard_html = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{BOT_STATUS}}": bot_status,
        "{{STATUS_COLOR}}": "var(--up)" if trading_engine.is_running else "var(--down)",
        "{{NAV}}": f"${last_nav:,.2f}",
        "{{DAILY_PNL}}": f"{daily_pnl:+,.2f}",
        "{{DAILY_PCT}}": f"{daily_pct:+.2f}%",
        "{{DAILY_SIGN}}": "up" if daily_pnl >= 0 else "down",
        "{{UNREALIZED_PNL}}": f"{unrealized_pnl:+,.2f}",
        "{{UNREALIZED_SIGN}}": "up" if unrealized_pnl >= 0 else "down",
        "{{TOTAL_RETURN}}": f"{total_return:+.2f}%",
        "{{RETURN_SIGN}}": "up" if total_return >= 0 else "down",
        "{{TRADING_PNL}}": f"{trading_total:+,.2f}",
        "{{TRADING_SIGN}}": "up" if trading_total >= 0 else "down",
        "{{SHARPE}}": sharpe_display,
        "{{SHARPE_SIGN}}": "up" if rolling_sharpe is not None and rolling_sharpe >= 0 else "down",
        "{{PORTFOLIO_HEAT}}": f"{portfolio_heat:.1f}%",
        "{{HEAT_WIDTH}}": f"{min(max(portfolio_heat, 0), 100):.1f}",
        "{{HEAT_COLOR}}": "var(--down)" if portfolio_heat > 20 else "var(--up)",
        "{{LAST_UPDATED}}": now_utc.strftime("%b %d, %H:%M UTC"),
        "{{PLOT_HTML}}": plot_html,
        "{{POSITION_ROWS}}": position_rows,
        "{{RECENT_TRADES}}": recent_trades_html,
    }
    for placeholder, value in replacements.items():
        dashboard_html = dashboard_html.replace(placeholder, value)
    return HTMLResponse(dashboard_html)


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
