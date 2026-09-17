# IrieTrade Trading Bot - User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Configuration Setup](#configuration-setup)
3. [Broker Setup](#broker-setup)
4. [API Keys and Secrets](#api-keys-and-secrets)
5. [Running the Bot](#running-the-bot)
6. [Getting Trades and Alerts](#getting-trades-and-alerts)
7. [Telegram Bot Setup](#telegram-bot-setup)
8. [Testing the Bot](#testing-the-bot)
9. [Troubleshooting](#troubleshooting)

---

## Getting Started

Before you begin, ensure you have:

- Python 3.11+ installed

- Git installed
- Access to trading accounts (IBKR for stocks/forex/metals/oils, KuCoin/Binance/etc. for crypto, Deriv for synthetics)

### Initial Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/Native-254/IrieTrade.git
   cd IrieTrade
   ```

2. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy the settings template:

   ```bash
   cp config/settings.yaml.template config/settings.yaml
   ```

5. Create your `.env` file for secrets (never commit this file):

   ```bash
   touch .env
   chmod 600 .env  # Restrict permissions to owner only
   ```

## Configuration Setup

The `config/settings.yaml.template` file provides a template for your configuration. Copy it to `config/settings.yaml` and customize it.

### Key Configuration Sections

#### General Settings

```yaml
general:
  bot_name: "IrieTrade"          # Name of your bot instance
  log_level: "INFO"              # Logging level (DEBUG, INFO, WARNING, ERROR)
  timezone: "[]     # Your local timezone
```

#### Trading Platforms

```yaml
trading:
  platform: "ib"                 # Default platform for single-platform operations
  platforms:                     # List of enabled platforms
    - "ib"                       # Interactive Brokers (stocks, forex, metals, oils)
    - "kucoin"                   # KuCoin exchange (crypto)
    - "deriv"                    # Deriv (synthetic indices)
  symbols: ["AAPL", "MSFT", "GOOGL"]  # Default symbols (used when no broker-specific symbols)
```

#### Symbols by Broker

Define which symbols each broker should trade:

```yaml
symbols_by_broker:
  ib:
    - "AAPL"                     # Stocks
    - "MSFT"
    - "GOOGL"
    - "EUR.USD"                  # Forex pairs
    - "GBP.USD"
    - "USD.JPY"
    - "XAUUSD"                   # Metals (Gold)
    - "XAGUSD"                   # Metals (Silver)
    - "CL=F"                     # Oils (WTI crude futures)
    - "BZ=F"                     # Oils (Brent crude futures)
  kucoin:
    - "ADA/USDT"                 # Crypto pairs
    - "XRP/USDT"
    - "SOL/USDT"
    # ... add more as needed
  deriv:
    - "R_10"                     # Volatility 10 Index
    - "R_50"                     # Volatility 50 Index
    - "R_100"                    # Volatility 100 Index
    - "BOOM500"                  # Boom 500 Index
    - "CRASH500"                 # Crash 500 Index
    - "XAUUSD"                   # Gold (also available on Deriv)
    - "XAGUSD"                   # Silver (also available on Deriv)
```

#### Exchange Configuration

Configure connection details for each exchange:

```yaml
exchanges:
  ib:
    port: 4002                   # IBKR Gateway/TWS port (4002 for paper, 4001/4003 for live)
    client_id: 1                 # Unique client ID for IBKR connection
    account_id: "DU12345678"      # Your IBKR account ID
  binance:
    testnet: true                # Set to false for live trading
  okx:
    testnet: true                # Set to false for live trading
  coinbase:
    testnet: true                # Set to false for live trading
  kraken:
    testnet: true                # Set to false for live trading
  kucoin:
    testnet: false               # Set to true for testnet, false for live
  deriv:
    # Deriv uses WebSocket API, no testnet flag needed for production
    # For testing, you would use a different app_id or endpoint
```

#### Risk Management

Adjust risk parameters according to your risk tolerance:

```yaml
risk_management:
  max_capital_per_trade: 0.02    # Max 2% of equity per trade
  max_portfolio_heat: 0.15       # Max 15% of equity at risk
  daily_loss_limit: 0.05         # Stop trading after 5% daily loss
  max_drawdown: 0.20             # Reduce positions after 20% drawdown from peak
  volatility_stop_multiplier: 2.0 # ATR multiplier for stop losses
  max_net_exposure: 0.5          # Max net exposure (long - short)
  max_gross_exposure: 2.5        # Max gross exposure (long + short)
  max_position_pct: 0.25         # Max 25% of equity in any single position
```

#### Strategies

Enable/disable trading strategies:

```yaml
strategies:
  active:
    - name: "TrendFollowingLongOnly"
      type: "technical"
      enabled: true
    - name: "TrendFollowing"
      type: "technical"
      enabled: true
    - name: "MeanReversion"
      type: "technical"
      enabled: true
    # ... other strategies
```

## Broker Setup

### Interactive Brokers (IBKR) Setup

IBKR is used for trading stocks, forex pairs, metals (gold/silver), and oils (WTI/Brent crude futures).

1. **Install IBKR Gateway or TWS**:
   - Download from <https://www.interactivebrokers.com/>
   - Install and log in to your account

2. **Configure API Settings**:
   - In IBKR Gateway/TWS: Configure > Settings > API > Settings
   - Check "Enable ActiveX and Socket Clients"
   - Set socket port to match your configuration (default 4002)
   - Check "Read-Only API" if you want to prevent accidental trades during setup
   - Uncheck "Read-Only API" when ready to trade live

3. **Paper Trading vs Live Trading**:
   - For paper trading: Use IBKR Paper Trading Gateway (port 4002 by default)
   - For live trading: Use IBKR Live Trading Gateway/TWS (configure port in gateway settings)

4. **IBKR Account Configuration**:
   - Find your Account ID in IBKR Gateway/TWS: Configure > Configuration > Account
   - This is the value you'll put in `exchanges.ib.account_id`

### KuCoin Setup

KuCoin is used for cryptocurrency trading.

1. **Create Account**:
   - Sign up at <https://www.kucoin.com/>
   - Complete KYC verification if required for your region

2. **Create API Key**:
   - Log in to KuCoin
   - Go to Account > API Management
   - Click "Create API"
   - Name your API key (e.g., "IrieTradeBot")
   - Enable permissions:
     - General (read account info)
     - Spot Trading (trade, cancel orders)
     - Wallet (read wallet info)
   - Google 2FA and Trading Password will be required
   - Save the API Key, Secret Key, and Passphrase securely

3. **Configuration**:

   ```yaml
   exchanges:
     kucoin:
       testnet: false  # Set to true for testing with KuCoin testnet
   ```

### Binance, OKX, Coinbase, Kraken Setup

These exchanges follow a similar pattern for crypto trading:

1. **Create Account** on the respective exchange
2. **Create API Key** with appropriate permissions:
   - Enable reading account info
   - Enable spot trading
   - Enable wallet info (if needed)
3. **Add to `.env` file**:

   ```bash
   # Binance
   BINANCE_API_KEY=your_binance_api_key
   BINANCE_SECRET=your_binance_secret
   
   # OKX
   OKX_API_KEY=your_okx_api_key
   OKX_SECRET=your_okx_secret
   OKX_PASSPHRASE=your_okx_passphrase
   
   # Coinbase
   COINBASE_API_KEY=your_coinbase_api_key
   COINBASE_SECRET=your_coinbase_secret
   
   # Kraken
   KRAKEN_API_KEY=your_kraken_api_key
   KRAKEN_SECRET=your_kraken_secret
   ```

### Deriv Setup

Deriv is used for trading synthetic indices (available 24/7, including weekends).

1. **Create Account**:
   - Sign up at <https://app.deriv.com/>
   - Complete verification if required

2. **Get API Token**:
   - Log in to Deriv
   - Go to Account > API Token
   - Click "Get New Token"
   - Name your token (e.g., "IrieTradeBot")
   - Copy the generated token securely

3. **Add to `.env` file**:

   ```bash
   DERIV_API_KEY=your_deriv_api_token_here
   ```

## API Keys and Secrets

All sensitive information should be stored in the `.env` file in the project root. This file is ignored by Git (see `.gitignore`).

### Example `.env` File Structure

```bash
# Interactive Brokers (if using Web API - not required for socket connection)
# IBKR_USERNAME=your_ibkr_username
# IBKR_PASSWORD=your_ibkr_password  # Only if using IBKR Web API

# Crypto Exchanges
KUCOIN_API_KEY=your_api_key
KUCOIN_SECRET=your_secret
KUCOIN_PASSPHRASE=your_password

BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET=your_binance_secret

OKX_API_KEY=your_okx_api_key
OKX_SECRET=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase

COINBASE_API_KEY=your_coinbase_api_key
COINBASE_SECRET=your_coinbase_secret

KRAKEN_API_KEY=your_kraken_api_key
KRAKEN_SECRET=your_kraken_secret

# Deriv
DERIV_API_KEY=your_deriv_api_key_here  # Get from app.deriv.com

# Notifications
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmnoPqrStUvwxYz1234567890
TELEGRAM_CHAT_ID=987654321  # Your personal chat ID for private alerts

# Email (Brevo recommended)
EMAIL_SENDER=your-email@gmail.com
EMAIL_RECIPIENT=your-email@gmail.com
EMAIL_BREVO_API_KEY=your_brevo_api_key_here
EMAIL_LOGO_URL=https://yourwebsite.com/logo.png

# AI Assistant (Optional)
AI_API_KEY=your_ai_api_key_here
AI_API_URL=https://api.openai.com/v1/chat/completions
AI_MODEL=gpt-4o-mini
```

**Important**:

- Never commit your `.env` file to version control
- Set file permissions to 600: `chmod 600 .env`
- Use environment-specific values (testnet vs live)

## Running the Bot

### 1. Paper Trading Mode (Recommended for Testing)

1. Ensure IBKR Gateway is running in Paper Trading mode
2. Verify your IBKR Gateway is listening on the correct port (default 4002)
3. Set paper trading to true in config:

   ```yaml
   execution:
     paper_trading: true
   ```

4. Start the bot:

   ```bash
   source venv/bin/activate
   python live/engine.py
   ```

### 2. Live Trading Mode

1. Ensure IBKR Gateway is running in Live Trading mode
2. Verify your IBKR Gateway is listening on the correct port
3. Set paper trading to false in config:

   ```yaml
   execution:
     paper_trading: false
   ```

4. Ensure you have sufficient funds and proper permissions
5. Start the bot:

   ```bash
   source venv/bin/activate
   python live/engine.py
   ```

### 3. Running as a Service (Production)

For production deployment, consider running as a systemd service:

1. Create a service file:

   ```ini
   [Unit]
   Description=IrieTrade Trading Bot
   After=network-online.target

   [Service]
   Type=simple
   WorkingDirectory=/home/yourusername/Projects\ 2026/trading_bot
   ExecStart=/home/yourusername/Projects\ 2026/trading_bot/venv/bin/python live/engine.py
   Restart=on-failure
   RestartSec=10
   Environment=PATH=/home/yourusername/Projects\ 2026/trading_bot/venv/bin
   Environment=PYTHONPATH=/home/yourusername/Projects\ 2026/trading_bot

   [Install]
   WantedBy=multi-user.target
   ```

2. Enable and start the service:

   ```bash
   sudo cp irietrade.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable irietrade
   sudo systemctl start irietrade
   ```

## Getting Trades and Alerts

### How the Bot Generates Trades

1. **Market Analysis**: The bot analyzes market data using enabled strategies
2. **Signal Generation**: Each strategy generates signals (ENTER_LONG, EXIT_LONG, ENTER_SHORT, EXIT_SHORT)
3. **Signal Resolution**: The position-aware resolver combines signals from all strategies
4. **Risk Checks**: The risk manager validates proposed trades against risk limits
5. **Order Execution**: Valid orders are sent to the appropriate broker
6. **Position Tracking**: The bot tracks open positions and manages stops/targets

### Alert Systems

The bot provides alerts through multiple channels:

#### Console Output

- Real-time logging to terminal
- Trade execution details
- Error and warning messages

#### Discord Alerts

- Rich embeds with trade details
- Set up via webhook URL in `.env`:

  ```
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  ```

#### Telegram Alerts

- Plain text messages with trade details
- Requires bot token and chat ID (see Telegram Bot Setup below)

#### Email Alerts

- Trade notifications and critical error messages
- Configured via Brevo API or SMTP in `.env`

#### Dashboard

- Real-time web interface at `http://localhost:8000/dashboard`
- Shows NAV, P&L, open positions, equity curve

## Telegram Bot Setup

To receive Telegram alerts, you need to create a Telegram bot and get your chat ID.

### Step 1: Create a Telegram Bot

1. Open Telegram and search for "@BotFather"
2. Start a chat with BotFather and send `/newbot`
3. Follow the prompts to name your bot and get a username
4. BotFather will provide you with an API token - save this securely
5. Your token will look like: `123456789:ABCdefGhIJKlmnoPqrStUvwxYz1234567890`

### Step 2: Get Your Chat ID

1. Start a chat with your new bot (send it any message)
2. Open your browser and visit:

    ```

   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```

   Replace `<YOUR_BOT_TOKEN>` with the token you got from BotFather
3. Look for `"chat":{"id":<YOUR_CHAT_ID>,...}` in the response
4. Your chat ID is a number (e.g., `987654321`)

### Step 3: Configure Telegram in Your Bot

Add to your `.env` file:

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmnoPqrStUvwxYz1234567890
TELEGRAM_CHAT_ID=987654321
```

### Step 4: Enable Telegram Alerts

In your `config/settings.yaml`:

```yaml
monitoring:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"   # Read from .env
    chat_id: "${TELEGRAM_CHAT_ID}"       # Read from .env
```

## Testing the Bot

### 1. Unit Tests

Run the test suite to verify individual components:

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

### 2. Broker Connection Tests

Test connections to each broker without placing orders:

```bash
# Test IBKR connection
source venv/bin/activate
python -c "
from execution.ib_broker import IBBroker
broker = IBBroker()
broker.connect()
print('IBKR connected:', broker.connected)
broker.disconnect()
"

# Test KuCoin connection
source venv/bin/activate
python -c "
from execution.kucoin_broker import KuCoinBroker
broker = KuCoinBroker({'testnet': True})
broker.connect()
print('KuCoin connected:', broker.connected)
broker.disconnect()
"

# Test Deriv connection
source venv/bin/activate
python -c "
from execution.deriv_broker import DerivBroker
broker = DerivBroker({})
broker.connect()
print('Deriv connected:', broker.connected)
broker.disconnect()
"
```

### 3. Strategy Tests

Test individual strategies with sample data:

```bash
source venv/bin/activate
python -c "
from strategies.trend_following_ls import TrendFollowingLS
import pandas as pd
import numpy as np

# Create sample data
data = pd.DataFrame({
    'close': np.random.randn(100).cumsum() + 100,
    'high': np.random.randn(100).cumsum() + 102,
    'low': np.random.randn(100).cumsum() + 98,
    'volume': np.random.randint(1000, 10000, 100)
})

strategy = TrendFollowingLS()
signals = strategy.generate_signals(data)
print('Generated signals:', signals.tail())
"
```

### 4. Backtesting

Run a backtest to see how strategies would have performed historically:

```bash
# Backtest a single symbol
source venv/bin/activate
python backtest/engine.py AAPL

# Backtest multiple symbols
source venv/bin/activate
python backtest/backtest_multi.py
```

### 5. Paper Trading Test

Run the bot in paper trading mode to observe behavior without risking real capital:

```bash
# Ensure paper trading is enabled
# execution.paper_trading: true in config/settings.yaml

source venv/bin/activate
python live/engine.py
```

Watch the logs for:

- Strategy signals
- Order placements (should show as paper trades)
- Position updates
- Alert notifications

### 6. Health Endpoint Test

When the bot is running, test the health endpoint:

```bash
curl http://127.0.0.1:8001/health
```

Should return JSON like:

```json
{
  "status": "ok",
  "ibkr_connected": true,
  "data_fresh": true,
  "last_error": null,
  "uptime_sec": 3600
}
```

## Configuration Tips

### Starting Simple

When first setting up the bot:

1. Start with just one broker (e.g., IBKR paper trading)
2. Enable only 1-2 simple strategies (e.g., TrendFollowingLS and MeanReversion)
3. Use a small subset of symbols (e.g., just AAPL and EUR.USD)
4. Use conservative risk settings
5. Monitor closely for the first few hours/days

### Gradual Expansion

As you become comfortable:

1. Add additional brokers (KuCoin for crypto, Deriv for synthetics)
2. Enable more strategies
3. Expand your symbol list
4. Adjust risk parameters to match your goals
5. Consider live trading with small amounts

### Regular Maintenance

1. Check logs regularly for errors or warnings
2. Monitor performance metrics in the dashboard
3. Review and adjust strategy parameters periodically
4. Keep dependencies updated: `pip install -r requirements.txt --upgrade`
5. Backtest strategy changes before deploying live

## Troubleshooting

### Common Issues

1. **Bot Not Connecting to Broker**
   - Verify broker is running and accepting API connections
   - Check port settings in config
   - Ensure API keys/permissions are correct
   - Check firewall/network settings

2. **No Trades Being Generated**
   - Check logs for strategy signals
   - Verify market data is being received
   - Confirm risk checks aren't blocking all trades
   - Ensure sufficient capital/buying power

3. **Alerts Not Working**
   - Verify API keys/tokens in `.env`
   - Check internet connectivity
   - Verify service-specific settings (Discord webhook, Telegram bot token/chat ID)
   - Test alert channels independently

4. **High CPU/Memory Usage**
   - Check for infinite loops in strategies
   - Verify data caching is working
   - Consider reducing scan frequency or symbol count
   - Check for memory leaks in long-running processes

### Getting Help

1. Check the logs in `logs/` directory for detailed error messages
2. Review the README.md for additional information
3. Look at the script_manual.md for service management information
4. Consider creating an issue on the GitHub repository if needed

---

## Important Disclaimer

**This bot is for educational purposes only.**

- Past performance does not guarantee future results
- Trading involves risk of loss
- Always test thoroughly in paper trading before committing real capital
- Start with small amounts when transitioning to live trading
- The bot's performance depends on market conditions, configuration, and proper maintenance
- You are solely responsible for any trading decisions and outcomes

By using this software, you acknowledge that you understand and accept these risks.
