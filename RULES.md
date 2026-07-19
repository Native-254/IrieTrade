# IrieTrade Trading Rules & Governance

## 1. Minimum Sample Size

- I will not judge the strategy’s edge until at least **50 closed trades** and **12 months** of live‑like paper trading data.
- I will not increase position size beyond paper limits until both thresholds are met.

## 2. Maximum Acceptable Drawdown

- If the account experiences a **peak‑to‑trough drawdown exceeding 20%**, I will halt trading and re‑evaluate the strategy.
- I will reduce position size by 50% after a drawdown of 15%.

## 3. Daily Loss Limit

- Trading will stop for the day if the daily P&L reaches **‑5%** of account equity.
- This rule is enforced automatically by the risk manager.

## 4. Review Frequency

- Every week, I will review: win rate, average win/loss, Sharpe ratio (30‑day), and gross/net exposure.
- Every month, I will run the backtesting script on a random sample of new symbols to check robustness.
- Every quarter, I will compare paper‑trading performance against backtest expectations.

## 5. Strategy Changes

- Any change to strategy parameters must be backtested over at least 5 years before going live in paper mode.
- Any new strategy must pass the same backtesting and paper‑trading validation as the existing ones.

## 6. Emotional Discipline

- I will not manually override the bot’s trades during live paper testing.
- I will not increase position size out of euphoria or decrease out of fear.

## 7. Go‑Live Criteria

- The bot will be considered ready for live capital only after:
  - 12 months of live‑like paper trading
  - Sharpe ratio > 0.5 (30‑day rolling)
  - Maximum drawdown < 15%
  - Win rate within one standard deviation of backtest average
  