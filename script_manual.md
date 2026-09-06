# IrieTrade Trading Bot - Manual Start/Stop Guide

## Overview

This manual explains how to manually start, stop, and manage the IrieTrade trading bot. The bot is designed to run autonomously via systemd, but can also be controlled manually for debugging or maintenance.

## Files Created

### 1. Launcher Script: `~/.local/bin/irietrade-start`

This is the main entry point for the trading bot. It handles:

- Pre-flight checks (time sync, virtual environment, IBKR gateway)
- Launching the trading engine with proper environment
- Supervision and automatic restart on failure
- Non-critical health endpoint verification

### 2. Wrapper Script: `~/.local/bin/irietrade-wrapper.sh`

Simple wrapper that sources environment and launches the main script:

```bash
#!/usr/bin/env bash
source "${HOME}/.config/irietrade/env" 2>/dev/null || true
cd "${HOME}/Projects 2026/trading_bot"
source venv/bin/activate
exec "${HOME}/.local/bin/irietrade-start"
```

### 3. Systemd Service: `~/.config/systemd/user/irietrade-wrapper.service`

User-level service for automatic startup and restart:

```ini
[Unit]
Description=IrieTrade Trading Bot (Wrapper)
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/irietrade-wrapper.sh
StandardOutput=journal
StandardError=journal
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### 4. Environment Template: `~/.config/irietrade/env`

Template for API keys and secrets (set permissions to 600):

```bash
# IBKR_USERNAME=[REDACTED]
# IBKR_PASSWORD=[REDACTED]  # Only if using IBKR Web API
# DISCORD_WEBHOOK_URL=[REDACTED]
# ALERT_EMAIL=[REDACTED]
```

## Manual Control Commands

### Start the Bot Manually

```bash
# Direct launch (logs to terminal)
~/.local/bin/irietrade-start

# Or via wrapper
~/.local/bin/irietrade-wrapper.sh
```

### Start via Systemd (Recommended for Auto-start)

```bash
# Start now
systemctl --user start irietrade-wrapper.service

# Enable auto-start on login
systemctl --user enable irietrade-wrapper.service

# Check status
systemctl --user status irietrade-wrapper.service

# View logs in real-time
journalctl -f -u irietrade-wrapper.service

# View last 50 lines
journalctl -u irietrade-wrapper.service -n 50
```

### Stop the Bot

```bash
# Via systemd
systemctl --user stop irietrade-wrapper.service

# Disable auto-start
systemctl --user disable irietrade-wrapper.service
```

### Restart the Bot

```bash
systemctl --user restart irietrade-wrapper.service
```

## Log Files

All engine logs are stored in: `~/Projects 2026/trading_bot/logs/`

Log files are named: `engine_YYYYMMDD_HHMMSS.log`

Each log contains:

- Engine initialization details
- Broker connection status (IBKR, KuCoin, etc.)
- Trading iterations and decisions
- Health endpoint availability
- Error messages and warnings

## Health Endpoint

When running, the bot provides a health endpoint at:

- URL: `http://127.0.0.1:8001/health`
- Returns JSON with:
  - `status`: "ok", "degraded", or "error"
  - `ibkr_connected`: boolean
  - `data_fresh`: boolean
  - `last_error`: string or null
  - `uptime_sec`: number

Dashboard (if enabled): `http://localhost:8000/dashboard`

## Troubleshooting

### Common Issues

1. **"Address already in use" on port 8001**
   - The launcher now automatically kills existing health-endpoint processes on port 8001 before starting
   - If persistent, manually run: `fuser -k 8001/tcp`

2. **IBKR Gateway not connecting**
   - Ensure IBKR Gateway is running
   - **Important Port Information**:
     - Port **4002** is used for **IBKR Paper Trading** (TWS/IBC in paper mode)
     - For **IBKR Live Trading**, you need to use the port configured in your IBKR Gateway application (typically 4001, 4003, or another port - check your gateway settings)
     - The launcher's `IB_PORT` variable (default 4002) should match the port your IBKR Gateway is listening on
     - To change the port for live trading, either:
       a) Change the `IB_PORT` variable in `~/.local/bin/irietrade-start` to match your live gateway port, OR
       b) Configure your IBKR Gateway to listen on port 4002 for live trading (via gateway configuration)
   - Check that you've accepted any API permission prompts in the gateway
   - Verify NOPASSWD sudoers rule for time sync is configured

3. **Module import errors**
   - The launcher sets PYTHONPATH to include the project root
   - Virtual environment is validated for required packages
   - If missing packages: `source venv/bin/activate && pip install ib_insync pandas numpy uvicorn fastapi`

4. **Bot not trading**
   - Check logs for broker connection status
   - Verify API keys in `.env` file (if using live trading)
   - Ensure sufficient funds and permissions on exchange accounts

## Safety Features

- Automatic restart on failure (systemd Restart=on-failure)
- Executable restart loop to prevent PID buildup
- Structured JSON logging for easy parsing
- Watchdog-friendly exit codes:
  - 0: Clean shutdown
  - 1: Transient error (will restart)
  - 2: Fatal error (requires investigation)
- IBKR gateway health checks with exponential backoff
- Time synchronization verification

## Customization

Adjust these variables in `~/.local/bin/irietrade-start`:

- `PROJECT_DIR`: Path to trading bot project
- `IB_PORT`: IBKR gateway port (default 4002 for paper trading - see troubleshooting for live trading)
- `MAX_INIT_RETRIES`: IBKR connection retries (default 5)
- `RETRY_BASE_DELAY`: Base delay for retries (default 5 seconds)
- `HEALTH_CHECK_URL`: Health endpoint URL (set to empty to disable)
