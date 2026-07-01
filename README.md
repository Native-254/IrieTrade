# IrieTrade Trading Bot

A fully automated, risk‑managed trading bot for the **NYSE** (via Interactive Brokers) with paper‑trading support, Discord/Telegram alerts, and a modular strategy engine. Built from scratch in Python.

![Bot Status](https://img.shields.io/badge/status-paper_trading-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

## ✨ Features

- **Multi‑strategy engine** – runs Trend‑Following (long/short), Mean‑Reversion, and more simultaneously.
- **Signal resolver** – merges signals from all strategies and ensures actions are always position‑aware (no accidental naked short selling).
- **Long/Short capability** – enters both long and short positions with hard bracket stops.
- **Interactive Brokers integration** – paper & live trading via `ib_async`.
- **Full risk management** – ATR‑based stops, Kelly‑dynamic position sizing, max portfolio heat, gross exposure limits, daily loss limits, and drawdown protection.
- **Trailing stops & partial exits** – automatically tightens stops and scales out of positions on exit signals.
- **Intra‑day data** – fetches 15‑minute bars with forced refresh for live trading.
- **Real‑time dashboard** – FastAPI dashboard showing open positions, equity curve, and trade history.
- **Data pipeline** – Yahoo Finance historical data with local Parquet caching and rate‑limit handling.
- **Notifications** – real‑time alerts to Discord (embeds) and Telegram.
- **Backtesting** – vectorized backtesting with `vectorbt` for strategy validation (currently single‑strategy).
- **Headless operation** – runs 24/7 on a VPS or local machine.
- **Modular design** – easy to swap data providers, brokers, or strategies.
- **Paper‑trading first** – built‑in simulation mode for safe testing.

## 🏗️ Architecture

trading_bot/
├── config/              # YAML configuration & templates
├── data/                # Data providers, manager & cache
├── strategies/          # Strategy classes & signal definitions
│   ├── signals.py
│   ├── base.py
│   ├── trend_following.py       # (original long‑only TrendFollowing)
│   ├── trend_following_ls.py    # (long/short TrendFollowingLS)
│   └── mean_reversion.py        # (Bollinger Bands + RSI)
├── backtest/            # Backtesting engine (vectorbt)
├── execution/           # Broker abstraction & IBKR implementation
├── risk/                # Risk manager (position sizing, limits)
├── monitoring/          # Discord & Telegram alerters, health API (FastAPI)
├── live/                # Main trading engine (orchestrator)
├── utils/               # Config loader, logger
├── logs/                # Runtime logs
├── data/raw/            # Parquet cache
├── requirements.txt     # Python dependencies
└── README.md

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Interactive Brokers Gateway (or TWS) with paper trading account
- (Optional) Discord webhook / Telegram bot for alerts

### Installation

```bash
git clone https://github.com/<your-username>/trading-bot.git
cd trading-bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

1. **Copy the settings template** and edit it:

   ```bash
   cp config/settings.yaml.template config/settings.yaml
   nano config/settings.yaml   # Set broker port, account, strategies, etc.
   ```

2. **Create a `.env` file** for secrets (never commit):

   ```bash
   echo "IB_ACCOUNT_ID=DU123456" >> .env
   echo "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/..." >> .env
   echo "TELEGRAM_BOT_TOKEN=..." >> .env
   echo "TELEGRAM_CHAT_ID=..." >> .env
   ```

### Run in Paper Trading Mode

1. **Start IB Gateway** (paper trading) with API enabled (port 4002).
2. **Launch the bot**:

   ```bash
   python live/engine.py
   ```

3. Watch the terminal logs and your Discord/Telegram for trade alerts.

> **Note:** By default, the bot runs the main iteration every hour at :01 (1 minute after the hour). To change the schedule, edit `live/engine.py` (look for `schedule.every`).

### 📈 Real‑Time Dashboard

Once the bot is running, open `http://localhost:8000/dashboard` to see your current positions, equity curve, and recent trades.

## 📊 Strategies

The bot supports multiple strategies simultaneously. Signals are collected from all active strategies and resolved through a central **Signal Resolver** that only generates trade actions consistent with your current positions.

### Active Strategies

- **TrendFollowingLS** – Long when 50‑SMA > 200‑SMA and RSI < 30; short when 50‑SMA < 200‑SMA and RSI > 70.
- **MeanReversion** – Long when price is below the lower Bollinger Band and RSI < 30; exit long when above the upper band and RSI > 70.
- **Breakout** (planned) – Donchian channel breakouts.

### Adding Your Own Strategy

1. Create a new class extending `BaseStrategy` in `strategies/`.
2. Implement `generate_signals(data)` and return a pandas Series of `Signal` enums (`ENTER_LONG`, `EXIT_LONG`, `ENTER_SHORT`, `EXIT_SHORT`, `HOLD`).
3. Register it in `live/engine.py`.

## 🛡️ Risk Management

The bot enforces strict risk rules before every trade:

- **Dynamic position sizing** – risk per trade is determined by a half‑Kelly criterion based on recent win/loss history (capped at 5% of equity).
- **Max portfolio heat** – total open risk cannot exceed a configurable fraction of equity (default 30%).
- **Gross exposure limit** – prevents total notional value of all positions from exceeding a safe multiple of equity.
- **Daily loss limit** – stops trading if the day’s P&L drops below a set threshold.
- **Max drawdown** – reduces position sizes after a configurable drawdown from peak equity.
- **ATR‑based stops** – initial and trailing stop‑losses are calculated using Average True Range.
- **Bracket orders for shorts** – short entries are placed with attached stop‑loss and take‑profit orders.

All values can be adjusted in `config/settings.yaml`.

## 🔔 Notifications

### Discord

Rich embeds with trade details (symbol, action, quantity, price) and error alerts. Set up via webhook URL in `.env`.

### Telegram

Plain text alerts. Requires a bot token and chat ID (obtain via @BotFather). *(Note: you may need to manually obtain your chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates` – a 403 error indicates an incorrect chat ID.)*

Both can be enabled/disabled independently in the config.

## 🧪 Backtesting

To evaluate a single strategy before live use:

```python
from backtest.engine import BacktestEngine
engine = BacktestEngine()
results = engine.run_backtest(
    strategy_name='TrendFollowing',
    symbol='AAPL',
    start_date='2020-01-01',
    end_date='2024-01-01',
    strategy_params={'fast_ma': 20, 'slow_ma': 50}
)
print(results)
```

> The backtesting engine currently supports one strategy at a time. Multi‑strategy, position‑aware backtesting is on the roadmap.

## ☁️ 24/7 Deployment (VPS)

The bot can run unattended on a free‑tier Oracle Cloud, Google Cloud, or AWS instance.

**Recommended: Oracle Cloud Always Free** (4 ARM cores, 24 GB RAM).

### Steps

1. Provision an Ubuntu VM.
2. Clone the repo, install dependencies, add config files.
3. Run as a `systemd` service for auto‑start and crash recovery.

A sample service file is provided in the Wiki.

## 📜 License

MIT – use, modify, and distribute freely.

## ⚠️ Disclaimer

This bot is for educational purposes. Use at your own risk. Past performance does not guarantee future results. Always test thoroughly in paper trading before committing real capital.
