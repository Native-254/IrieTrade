# IrieTrade

![IrieTrade](https://github.com/user-attachments/assets/8190264a-85b9-47dd-a0d5-f8f92d7347b9)

A fully automated, risk‑managed trading bot for the **NYSE** (via Interactive Brokers) and multiple crypto exchanges, with paper‑trading support, real‑time alerts, a modular strategy engine, and an interactive dashboard. Built from scratch in Python.

![Bot Status](https://img.shields.io/badge/status-paper_trading-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![Star this repo](https://img.shields.io/github/stars/Native-254/trading-bot?style=social)](https://github.com/Native-254/IrieTrade)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat)](CONTRIBUTING.md)

> **💡 Found a bug? Have an idea?**
[Open an issue](https://github.com/Native-254/IrieTrade/issues) – every suggestion helps this project grow!

## ✨ Features

- **Multi‑strategy engine** – runs Trend‑Following (Long/Short), Trend‑Following (Long Only), and Mean‑Reversion simultaneously.
- **Position‑aware signal resolver** – merges signals from all active strategies and resolves conflicts based on your current position, preventing accidental naked short selling and handling reversals safely.
- **Long/Short capability** – enters both long and short positions with hard bracket stops (IBKR) or market orders (crypto).
- **Multi‑platform trading** – supports Interactive Brokers (IBKR), Binance, OKX, Coinbase, Kraken, KuCoin, and a ready‑to‑use stub for the Nairobi Securities Exchange (NSE).
- **Full risk management** – ATR‑based stops, Kelly‑dynamic position sizing, max portfolio heat, gross/ net exposure limits, daily loss limits, drawdown protection, single‑name limits, and an earnings blackout filter.
- **Trailing stops & partial exits** – automatically tightens stop orders and scales out of positions on exit signals.
- **Intra‑day data** – fetches 15‑minute bars with forced refresh for live trading.
- **Real‑time dashboard** – FastAPI dashboard showing NAV, daily P&L, unrealised P&L, equity curve, open positions, and trade history.
- **Realistic paper‑trading simulation** – simulated slippage, commissions, partial fills, and short‑availability checks make the paper account behave exactly like a live account.
- **Position synchronisation** – internal positions are reconciled with IBKR’s reported positions every iteration.
- **Custom API** – REST endpoints for signals, positions, and performance (`/api/signals`, `/api/positions`, `/api/performance`).
- **Data pipeline** – Yahoo Finance historical data with local Parquet caching and rate‑limit handling.
- **Notifications** – real‑time alerts to Discord (embeds), Telegram, and Email (Brevo API or SMTP).
- **Backtesting** – a custom loop‑based backtester that runs the exact same strategy classes and signal resolver as the live engine, supporting multi‑strategy, position‑aware simulations.
- **Headless operation** – runs 24/7 on a VPS or local machine.
- **Modular design** – easy to swap data providers, brokers, or strategies.
- **Dependency injection** – all major components can be injected for easy unit testing.
- **Onboarding wizard** – a browser‑based setup tool that writes your `.env` and `settings.yaml` without manual editing.

## 🏗️ Architecture

```plaintext
trading_bot/
├── config/
│   ├── settings.yaml          # Main runtime configuration
│   └── setup.html             # Onboarding wizard UI
├── data/
│   ├── manager.py             # Data fetching & caching
│   ├── provider.py            # Abstract data provider
│   └── yahoo_provider.py      # Yahoo Finance implementation
├── strategies/
│   ├── signals.py             # Signal enum
│   ├── base.py                # Base strategy class
│   ├── trend_following_ls.py  # Long/Short trend following (50/200 SMA + RSI)
│   ├── trend_following_long_only.py
│   └── mean_revisions.py      # Bollinger Bands + RSI
├── backtest/
│   ├── engine.py              # Loop‑based backtester (uses live resolver)
│   └── backtest_multi.py      # Multi‑symbol backtest runner
├── execution/
│   ├── broker.py              # Abstract broker interface
│   ├── ib_broker.py           # Interactive Brokers (bracket orders)
│   ├── binance_broker.py      # ccxt
│   ├── okx_broker.py
│   ├── coinbase_broker.py
│   ├── kraken_broker.py
│   ├── kucoin_broker.py
│   ├── nse_broker.py          # NSE stub (placeholder)
│   ├── deriv_broker.py
│   ├── olymprade_broker.py
│   ├── oneinch_broker.py
│   ├── web3_dex_broker.py
│   └── broker_manager.py      # Dynamically loads enabled brokers
├── risk/
│   ├── manager.py             # Risk rules & exposure calculation
│   └── position_manager.py    # Per‑broker position tracking
├── monitoring/
│   ├── api.py                 # FastAPI dashboard & REST API
│   ├── discord_alerter.py
│   ├── telegram_alerter.py
│   └── email_alerter.py       # Brevo + SMTP
├── live/
│   └── engine.py              # Main orchestrator – multi‑broker loop
├── utils/
│   ├── config.py              # YAML loader with env var override
│   └── logger.py              # Loguru configuration
├── logs/                      # Runtime logs & trade journal
├── tests/                     # Unit & integration tests
├── requirements.txt
└── README.md
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Interactive Brokers Gateway (or TWS) with paper trading account (for IBKR trading)
- (Optional) API keys for crypto exchanges
- (Optional) Discord webhook, Telegram bot, or email account for alerts

### Installation

```bash
git clone https://github.com/Native-254/trading-bot.git
cd trading-bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

1. **Copy the settings template** and edit it:

   ```bash
   cp config/settings.yaml.template config/settings.yaml
   nano config/settings.yaml   # Set platform(s), symbols, risk parameters, etc.
   ```

2. **Create a `.env` file** for secrets (never commit):

   ```bash
   echo "IB_ACCOUNT_ID=DU123456" >> .env
   echo "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/..." >> .env
   echo "TELEGRAM_BOT_TOKEN=..." >> .env
   echo "TELEGRAM_CHAT_ID=..." >> .env
   echo "EMAIL_SENDER=your-email@gmail.com" >> .env
   echo "EMAIL_RECIPIENT=recipient@email.com" >> .env
   # For Brevo (primary email transport)
   echo "EMAIL_BREVO_API_KEY=your_brevo_api_key" >> .env
   # For Gmail SMTP (fallback)
   echo "EMAIL_PASSWORD=your-16-char-app-password" >> .env
   # Add any crypto exchange keys as needed (see docs)
   ```

3. **Optional: Use the onboarding wizard**  
   Once the bot is running, visit `http://localhost:8000/setup` to configure brokers and symbols through a web UI – no manual YAML editing required.

### Run in Paper Trading Mode

1. **Start IB Gateway** (paper trading) with API enabled (port 4002).
2. **Set** `paper_trading: false` in `config/settings.yaml` – the bot will send real bracket orders to the paper account.
3. **Enable realistic simulations** in the same config (slippage, commissions, partial fills, short checks – all on by default).
4. **Launch the bot**:

   ```bash
   python live/engine.py
   ```

5. Watch the terminal logs and your Discord/Telegram for trade alerts.

> **Note:** By default, the bot runs the main iteration every hour at :01 (1 minute after the hour). To change the schedule, edit `live/engine.py` (look for `schedule.every`).

### 📈 Real‑Time Dashboard

Once the bot is running, open `http://localhost:8000/dashboard` to see your current NAV, open positions, equity curve, and recent trades.

## 📊 Strategies

The bot supports multiple strategies simultaneously. Signals are collected from all active strategies and resolved through a central **Signal Resolver** that only generates trade actions consistent with your current positions.

### Active Strategies

- **TrendFollowingLS** – Long when 50‑SMA > 200‑SMA and RSI < 30; short when 50‑SMA < 200‑SMA and RSI > 70.
- **TrendFollowingLongOnly** – Same as above but only takes long entries (no short selling).
- **MeanReversion** – Long when price is below the lower Bollinger Band and RSI < 30; exit long when above the upper band and RSI > 70.
- **Breakout** (planned) – Donchian channel breakouts.

### Resolver Logic

- If already long:
  - `EXIT_LONG` → sell to close.
  - `ENTER_SHORT` → sell to close first (reversal will be evaluated on the next bar).
- If already short:
  - `EXIT_SHORT` → buy to cover.
  - `ENTER_LONG` → buy to cover first.
- If flat:
  - `ENTER_LONG` alone → buy.
  - `ENTER_SHORT` alone → sell short.
  - Both `ENTER_LONG` and `ENTER_SHORT` → no action (conflict).

This prevents naked shorts, double entries, and accidental reversals.

### Adding Your Own Strategy

1. Create a new class extending `BaseStrategy` in `strategies/`.
2. Implement `generate_signals(data)` and return a pandas Series of `Signal` enums (`ENTER_LONG`, `EXIT_LONG`, `ENTER_SHORT`, `EXIT_SHORT`, `HOLD`).
3. Register it in `live/engine.py` (add to the strategy loader).

## 🛡️ Risk Management

The bot enforces strict risk rules before every trade:

- **Dynamic position sizing** – risk per trade is determined by a half‑Kelly criterion based on recent win/loss history (capped at 5% of equity).
- **Max portfolio heat** – total open risk cannot exceed a configurable fraction of equity (default 15%).
- **Gross exposure limit** – prevents total notional value of all positions from exceeding a safe multiple of equity (default 1.5x).
- **Net exposure limit** – caps the absolute difference between long and short notional.
- **Single‑name limit** – no single position may exceed 20% of equity.
- **Daily loss limit** – stops trading if the day’s P&L drops below a set threshold.
- **Max drawdown** – reduces position sizes after a configurable drawdown from peak equity.
- **ATR‑based stops** – initial and trailing stop‑losses are calculated using Average True Range.
- **Bracket orders for longs and shorts** – entries are protected with attached stop‑loss and take‑profit orders (IBKR only).
- **Short‑availability check** – verifies that shares are available to short before placing a short order.
- **Earnings blackout filter** – avoids opening new positions near earnings announcements.

All values can be adjusted in `config/settings.yaml`.

## ⚡ Execution & Realistic Paper Simulation

The bot connects to Interactive Brokers via `ib_async`. In paper mode, you can enable a set of realistic simulation features that make the paper account behave indistinguishably from a live account:

| Feature | Description | Config Key |
| --------- | ------------- | ------------ |
| **Slippage** | Adds a small adverse price movement (default 0.05%) to every trade | `simulate_slippage`, `slippage_percent` |
| **Commissions** | Charges realistic IBKR stock commissions ($0.005/share, min $1, max 1%) | `simulate_commissions`, `commission_*` |
| **Partial fills** | Randomly fills only 80‑100% of your order to simulate real market behaviour | `simulate_partial_fills`, `partial_fill_min_ratio` |
| **Short availability** | Checks with IBKR that shares are available to short before sending a short order | `short_availability_check` |

These are enabled by default in paper mode. Disable them when you switch to a live account.

## 🔔 Alerts & Notifications

### Discord

Rich embeds with trade details (symbol, action, quantity, price) and error alerts. Set up via webhook URL in `.env`.

### Telegram

Plain text alerts. Requires a bot token and chat ID (obtain via @BotFather).  
*(Note: you may need to manually obtain your chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates` – a 403 error indicates an incorrect chat ID.)*

### Email

Trade alerts and critical error messages sent via **Brevo API** (primary) or Gmail SMTP (fallback).  
Add the following to your `.env`:

```bash
EMAIL_SENDER=your-email@gmail.com
EMAIL_RECIPIENT=recipient@email.com
EMAIL_BREVO_API_KEY=your-brevo-api-key    # for Brevo
EMAIL_PASSWORD=your-gmail-app-password    # for SMTP fallback
```

All three channels can be enabled/disabled independently.

## 🧪 Backtesting

The backtesting engine now **mimics the live bot exactly**. It re‑uses the same strategy classes, signal resolver, and risk checks in a loop over historical data.

To backtest all symbols in your configuration:

```bash
python backtest_multi.py
```

Or programmatically:

```python
from backtest.engine import BacktestEngine
from data.manager import DataManager

data_mgr = DataManager()
df = data_mgr.get_data("AAPL", start_date="2020-01-01", end_date="2024-01-01", interval="1d")

engine = BacktestEngine()
result = engine.run("AAPL", df, start_date="2020-01-01", end_date="2024-01-01")

print("Final capital:", result['final_capital'])
print("Number of trades:", result['total_trades'])
print("Equity curve:", result['equity_curve'])
```

The engine simulates slippage, commissions, trailing stops, and the full position‑aware resolver on each bar.

## ☁️ 24/7 Deployment (VPS)

The bot can run unattended on a free‑tier Oracle Cloud, Google Cloud, or AWS instance.

**Recommended: Oracle Cloud Always Free** (4 ARM cores, 24 GB RAM).

### Steps

1. Provision an Ubuntu VM.
2. Clone the repo, install dependencies, add config files.
3. Run as a `systemd` service for auto‑start and crash recovery.

A sample service file is provided in the Wiki.

## 🌍 Multi‑Platform Support

IrieTrade already supports multiple centralised exchanges through a unified `Broker` interface. Each broker is instantiated by the `BrokerManager` based on your `platforms` list in `settings.yaml`.

| Platform | Status |
| ---------- | -------- |
| **Interactive Brokers** | Fully functional (paper & live bracket orders) |
| **Binance** | ccxt – market orders only |
| **OKX** | ccxt – market orders only |
| **Coinbase** | ccxt – market orders only |
| **Kraken** | ccxt – market orders only |
| **KuCoin** | ccxt – market orders only |
| **NSE (Nairobi)** | Stub ready – no trading API yet, but infrastructure in place |

To add a new exchange, create a class implementing `Broker`, register it in `broker_manager.py`, and add its configuration.

## 📜 License

MIT – use, modify, and distribute freely.

## ⚠️ Disclaimer

This bot is for educational purposes. Use at your own risk. Past performance does not guarantee future results. Always test thoroughly in paper trading before committing real capital.
